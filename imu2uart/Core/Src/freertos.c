/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          : freertos.c
  * Description        : Code for freertos applications
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "FreeRTOS.h"
#include "task.h"
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "stdio.h"
#include "i2c.h"
#include "lsm6dsv16x.h"
#include "usart.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* Poll cadence. The IMU runs at 240 Hz but one register read costs ~2 ms on a
   100 kHz bus, and draining the FIFO adds most of another millisecond, so the
   loop lands near 100 Hz; every IMU_OUTPUT_EVERY polls the accumulated samples
   are averaged and one CSV line goes out (~35 Hz). Keep the line rate
   comfortably under 115200/(chars per line) or the UART becomes the bottleneck
   instead of the bus. */
#define IMU_POLL_MS              5U
#define IMU_OUTPUT_EVERY         3U

/* While no fused sample has ever arrived, say so with the evidence that would
   explain why: how many FIFO words were drained and what tag the last one had.
   Rate-limited because this prints in the middle of the CSV stream. */
#define IMU_SFLP_WARN_MS         3000U

/* Consecutive "data not ready" reads before the device counts as stopped. At
   ~140 Hz this is about 3.5 s, far longer than a momentary stall, and it is the
   only way out of an IMU that is powered but has stopped converting. */
#define IMU_MAX_BUSY_STREAK      500U

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN Variables */

/* USER CODE END Variables */
/* Definitions for defaultTask */
osThreadId_t defaultTaskHandle;
const osThreadAttr_t defaultTask_attributes = {
  .name = "defaultTask",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};

/* Private function prototypes -----------------------------------------------*/
/* USER CODE BEGIN FunctionPrototypes */
static void imu_delay_us(uint32_t us);
static void imu_bus_recover(void);
static long imu_e4(float value);
/* USER CODE END FunctionPrototypes */

void StartDefaultTask(void *argument);

void MX_FREERTOS_Init(void); /* (MISRA C 2004 rule 8.1) */

/**
  * @brief  FreeRTOS initialization
  * @param  None
  * @retval None
  */
void MX_FREERTOS_Init(void) {
  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  /* add queues, ... */
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of defaultTask */
  defaultTaskHandle = osThreadNew(StartDefaultTask, NULL, &defaultTask_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

}

/* USER CODE BEGIN Header_StartDefaultTask */
/**
  * @brief  Function implementing the defaultTask thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_StartDefaultTask */
void StartDefaultTask(void *argument)
{
  /* USER CODE BEGIN StartDefaultTask */
  LSM6DSV16X_RawData imu_data;
  LSM6DSV16X_Sflp sflp;
  HAL_StatusTypeDef imu_status;
  HAL_StatusTypeDef sflp_status;
  uint8_t imu_ready = 0U;
  uint8_t read_error_count = 0U;
  uint8_t output_divider = 0U;
  uint8_t sample_count = 0U;
  uint16_t busy_streak = 0U;
  int32_t accel_sum[3] = {0, 0, 0};
  int32_t gyro_sum[3] = {0, 0, 0};
  /* Last fused attitude, scalar-first. All four zero is the "the chip has not
     produced one yet" sentinel the host looks for. */
  float quat[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  uint32_t sflp_seen = 0U;
  uint32_t fifo_words = 0U;
  uint8_t fifo_tag = 0U;
  uint32_t sflp_warn_tick = 0U;

  printf("\r\nSTM32F411 LSM6DSV16X I2C reader\r\n");
  printf("I2C1: PB6=SCL, PB7=SDA, 100kHz; UART1: 115200 8N1\r\n");

  /* Infinite loop */
  for(;;)
  {
    if (imu_ready == 0U)
    {
      /* Unlock the bus before every detection attempt. A HAL I2C state machine
         that latched up never recovers on its own, and re-running Init alone
         leaves it latched: IsDeviceReady just fails again forever. */
      imu_bus_recover();

      imu_status = LSM6DSV16X_Init(&hi2c1);
      if (imu_status == HAL_OK)
      {
        imu_ready = 1U;
        read_error_count = 0U;
        busy_streak = 0U;
        output_divider = 0U;
        sample_count = 0U;
        sflp_seen = 0U;
        fifo_words = 0U;
        fifo_tag = 0U;
        sflp_warn_tick = HAL_GetTick();
        quat[0] = 0.0f;
        quat[1] = 0.0f;
        quat[2] = 0.0f;
        quat[3] = 0.0f;
        printf("LSM6DSV16X detected at 0x%02X (WHO_AM_I=0x70)\r\n",
               (unsigned int)LSM6DSV16X_GetAddress());
        if (LSM6DSV16X_EnableSflp() == HAL_OK)
        {
          printf("SFLP enabled: game rotation vector at 120 Hz, read from the FIFO\r\n");
        }
        else
        {
          printf("SFLP unavailable; quaternion columns will stay zero\r\n");
        }
        /* q*_e4 is a quaternion component times 10000: printf cannot format a
           float here, and a fixed-point column keeps the CSV self-describing. */
        printf("time_ms,ax_mg,ay_mg,az_mg,gx_mdps,gy_mdps,gz_mdps,"
               "qw_e4,qx_e4,qy_e4,qz_e4\r\n");
      }
      else
      {
        printf("LSM6DSV16X not found at 0x6A/0x6B; retrying...\r\n");
        osDelay(1000U);
      }
      continue;
    }

    imu_status = LSM6DSV16X_ReadRaw(&imu_data);
    sflp_status = LSM6DSV16X_ReadSflp(&sflp);

    if (sflp_status == HAL_OK)
    {
      /* Straight from the chip's fusion block -- no host-side filtering. It
         ships x/y/z only; the driver rebuilds w from the unit-norm constraint. */
      quat[0] = sflp.quat.w;
      quat[1] = sflp.quat.x;
      quat[2] = sflp.quat.y;
      quat[3] = sflp.quat.z;
      ++sflp_seen;
    }
    else if (sflp_status == HAL_BUSY)
    {
      if (sflp.words != 0U)
      {
        fifo_words += (uint32_t)sflp.words;
        fifo_tag = sflp.tag;
      }
    }
    else
    {
      /* Bus trouble on the FIFO read: let the raw path's error counter see it. */
      imu_status = HAL_ERROR;
    }

    if (imu_status == HAL_OK)
    {
      read_error_count = 0U;
      busy_streak = 0U;

      /* The ODR is faster than this loop, so each read returns the newest
         conversion and the ones in between are gone. Averaging what is read
         before decimating is what keeps their quantisation out of the stream. */
      accel_sum[0] += imu_data.accel_x;
      accel_sum[1] += imu_data.accel_y;
      accel_sum[2] += imu_data.accel_z;
      gyro_sum[0] += imu_data.gyro_x;
      gyro_sum[1] += imu_data.gyro_y;
      gyro_sum[2] += imu_data.gyro_z;
      ++sample_count;

      if (++output_divider >= IMU_OUTPUT_EVERY)
      {
        output_divider = 0U;
        printf("%lu,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld,%ld\r\n",
               (unsigned long)HAL_GetTick(),
               (long)LSM6DSV16X_AccelRawToMg((int16_t)(accel_sum[0] / (int32_t)sample_count)),
               (long)LSM6DSV16X_AccelRawToMg((int16_t)(accel_sum[1] / (int32_t)sample_count)),
               (long)LSM6DSV16X_AccelRawToMg((int16_t)(accel_sum[2] / (int32_t)sample_count)),
               (long)LSM6DSV16X_GyroRawToMdps((int16_t)(gyro_sum[0] / (int32_t)sample_count)),
               (long)LSM6DSV16X_GyroRawToMdps((int16_t)(gyro_sum[1] / (int32_t)sample_count)),
               (long)LSM6DSV16X_GyroRawToMdps((int16_t)(gyro_sum[2] / (int32_t)sample_count)),
               imu_e4(quat[0]),
               imu_e4(quat[1]),
               imu_e4(quat[2]),
               imu_e4(quat[3]));
        accel_sum[0] = 0;
        accel_sum[1] = 0;
        accel_sum[2] = 0;
        gyro_sum[0] = 0;
        gyro_sum[1] = 0;
        gyro_sum[2] = 0;
        sample_count = 0U;
      }
    }
    else if (imu_status == HAL_BUSY)
    {
      /* Routine: the loop polls a little faster than the device converts. A
         streak this long means the device is not converting at all. */
      if (++busy_streak >= IMU_MAX_BUSY_STREAK)
      {
        printf("LSM6DSV16X stopped producing data; reinitializing...\r\n");
        imu_ready = 0U;
      }
    }
    else
    {
      ++read_error_count;
      if (read_error_count >= 3U)
      {
        printf("LSM6DSV16X I2C read failed; reinitializing...\r\n");
        imu_ready = 0U;
      }
    }

    if ((sflp_seen == 0U) &&
        ((HAL_GetTick() - sflp_warn_tick) >= IMU_SFLP_WARN_MS))
    {
      sflp_warn_tick = HAL_GetTick();
      printf("SFLP waiting: no game rotation vector yet "
             "(%lu FIFO words drained, last tag 0x%02X)\r\n",
             (unsigned long)fifo_words, (unsigned int)fifo_tag);
    }

    osDelay(IMU_POLL_MS);
  }
  /* USER CODE END StartDefaultTask */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Application */

/* Quaternion component -> units of 1e-4. printf on this target has no float
   conversion, so the CSV carries fixed point and the column name carries the
   scale. */
static long imu_e4(float value)
{
  return (long)((value >= 0.0f) ? ((value * 10000.0f) + 0.5f)
                                : ((value * 10000.0f) - 0.5f));
}

/* Coarse microsecond busy wait: a few cycles per iteration on the 100 MHz core,
   which is all the timing precision an I2C bus recovery needs. */
static void imu_delay_us(uint32_t us)
{
  volatile uint32_t cycles = us * 8U;

  while (cycles-- != 0U)
  {
    __NOP();
  }
}

/**
  * @brief  Reset I2C1 and bit-bang the bus free.
  * @note   A HAL I2C that latched mid-transfer stays latched: DeInit hands
  *         PB6/PB7 back as plain GPIO, nine SCL pulses walk a slave that is
  *         holding SDA low through its shift register, a manual STOP ends the
  *         frame, and MX_I2C1_Init puts the peripheral (and AF4 on the pins)
  *         back. Skipping any of this leaves the port dead until power cycle.
  */
static void imu_bus_recover(void)
{
  GPIO_InitTypeDef gpio = {0};
  uint8_t pulse;

  HAL_I2C_DeInit(&hi2c1);

  __HAL_RCC_GPIOB_CLK_ENABLE();
  gpio.Pin = GPIO_PIN_6 | GPIO_PIN_7;
  gpio.Mode = GPIO_MODE_OUTPUT_OD;
  gpio.Pull = GPIO_PULLUP;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(GPIOB, &gpio);

  /* Both lines released; the pull-ups take them high. */
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_SET);
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
  imu_delay_us(10U);

  for (pulse = 0U; pulse < 9U; ++pulse)
  {
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_RESET);
    imu_delay_us(5U);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_SET);
    imu_delay_us(5U);
  }

  /* STOP: SDA low to high while SCL is high. */
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_RESET);
  imu_delay_us(5U);
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_6, GPIO_PIN_SET);
  imu_delay_us(5U);
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
  imu_delay_us(5U);

  /* HAL_I2C_Init rather than MX_I2C1_Init: that wrapper calls Error_Handler on
     failure, and Error_Handler disables interrupts and spins forever. A recovery
     routine that can turn a transient bus fault into a bridge that is silent
     until the next power cycle is worse than no recovery routine at all. If the
     re-arm fails here the main loop simply tries again next iteration.
     MspInit still runs (the state was put back to RESET by DeInit), so PB6/PB7
     get their AF4 configuration and the I2C1 clock is re-enabled. */
  if (HAL_I2C_Init(&hi2c1) != HAL_OK)
  {
    printf("I2C1 re-init failed; will retry\r\n");
  }
}

int fputc(int ch,FILE *f)
{
	HAL_UART_Transmit(&huart1,(uint8_t *)&ch,1,0xFFFF);
	return ch;
}
/* USER CODE END Application */

