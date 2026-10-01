/**
  ******************************************************************************
  * @file    lsm6dsv16x.c
  * @brief   I2C driver for the ST LSM6DSV16X IMU.
  *
  * The configuration follows DS13510 Rev 4:
  *   - accelerometer: 240 Hz, high-performance mode, +/-2 g, LPF2 enabled
  *   - gyroscope:     240 Hz, high-performance mode, +/-250 dps, LPF1 enabled
  *   - BDU and automatic register address increment enabled
  *   - SFLP (in-chip sensor fusion): game rotation vector, 120 Hz, into the FIFO
  ******************************************************************************
  */

#include "lsm6dsv16x.h"

#include <math.h>
#include <string.h>

#define LSM6DSV16X_REG_FUNC_CFG_ACCESS   0x01U
#define LSM6DSV16X_REG_EMB_FUNC_EN_A     0x04U
#define LSM6DSV16X_REG_FIFO_CTRL3        0x09U
#define LSM6DSV16X_REG_FIFO_CTRL4        0x0AU
#define LSM6DSV16X_REG_WHO_AM_I          0x0FU
#define LSM6DSV16X_REG_CTRL1             0x10U
#define LSM6DSV16X_REG_CTRL2             0x11U
#define LSM6DSV16X_REG_CTRL3             0x12U
#define LSM6DSV16X_REG_CTRL6             0x15U
#define LSM6DSV16X_REG_CTRL7             0x16U
#define LSM6DSV16X_REG_CTRL8             0x17U
#define LSM6DSV16X_REG_CTRL9             0x18U
#define LSM6DSV16X_REG_FIFO_STATUS1      0x1BU
#define LSM6DSV16X_REG_STATUS            0x1EU
#define LSM6DSV16X_REG_OUTX_L_G          0x22U
#define LSM6DSV16X_REG_EMB_FUNC_FIFO_EN_A 0x44U
#define LSM6DSV16X_REG_SFLP_ODR          0x5EU
#define LSM6DSV16X_REG_FIFO_DATA_OUT_TAG 0x78U

#define LSM6DSV16X_CTRL3_SW_RESET        0x01U
#define LSM6DSV16X_CTRL3_IF_INC          0x04U
#define LSM6DSV16X_CTRL3_BDU             0x40U
#define LSM6DSV16X_CTRL3_BOOT            0x80U

#define LSM6DSV16X_STATUS_XLDA           0x01U
#define LSM6DSV16X_STATUS_GDA            0x02U

/* Embedded-function registers (SFLP_ODR, EMB_FUNC_EN_A, EMB_FUNC_FIFO_EN_A) are
   only reachable while this bit is set; otherwise the same addresses mean
   something else in the main page and the writes silently go nowhere. */
#define LSM6DSV16X_FUNC_CFG_EMB_ACCESS   0x80U

#define LSM6DSV16X_EMB_FUNC_EN_A_SFLP_GAME      0x02U
#define LSM6DSV16X_EMB_FUNC_FIFO_EN_A_SFLP_GAME 0x02U

/* SFLP_GAME_ODR[2:0]: 15/30/60/120/240/480 Hz. 120 Hz leaves the 240 Hz
   sensor ODR two samples per fused output and keeps FIFO traffic light. */
#define LSM6DSV16X_SFLP_ODR_120HZ        0x03U

/* FIFO_MODE[2:0] = 110: stream/continuous. When the buffer fills the oldest
   words are dropped, so a host that stalls loses resolution but never wedges. */
#define LSM6DSV16X_FIFO_MODE_STREAM      0x06U

/* Each FIFO word is one tag byte plus six data bytes, read in one burst. */
#define LSM6DSV16X_FIFO_WORD_LEN         7U
#define LSM6DSV16X_FIFO_MAX_WORDS        4U

/* FIFO tag of the SFLP game rotation vector. */
#define LSM6DSV16X_FIFO_TAG_SFLP_GAME    0x13U

#define LSM6DSV16X_I2C_TIMEOUT_MS        100U
#define LSM6DSV16X_RESET_TIMEOUT_MS      100U

static I2C_HandleTypeDef *lsm6dsv16x_i2c;
static uint16_t lsm6dsv16x_device_address;

static HAL_StatusTypeDef LSM6DSV16X_ReadRegister(uint8_t reg, uint8_t *data,
                                                 uint16_t length)
{
  if ((lsm6dsv16x_i2c == NULL) || (lsm6dsv16x_device_address == 0U) ||
      (data == NULL) || (length == 0U))
  {
    return HAL_ERROR;
  }

  return HAL_I2C_Mem_Read(lsm6dsv16x_i2c, lsm6dsv16x_device_address, reg,
                          I2C_MEMADD_SIZE_8BIT, data, length,
                          LSM6DSV16X_I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef LSM6DSV16X_WriteRegister(uint8_t reg, uint8_t value)
{
  if ((lsm6dsv16x_i2c == NULL) || (lsm6dsv16x_device_address == 0U))
  {
    return HAL_ERROR;
  }

  return HAL_I2C_Mem_Write(lsm6dsv16x_i2c, lsm6dsv16x_device_address, reg,
                           I2C_MEMADD_SIZE_8BIT, &value, 1U,
                           LSM6DSV16X_I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef LSM6DSV16X_WaitWhileSet(uint8_t reg, uint8_t mask)
{
  uint32_t start_tick = HAL_GetTick();
  uint8_t value;

  do
  {
    if (LSM6DSV16X_ReadRegister(reg, &value, 1U) != HAL_OK)
    {
      return HAL_ERROR;
    }
    if ((value & mask) == 0U)
    {
      return HAL_OK;
    }
    HAL_Delay(1U);
  } while ((HAL_GetTick() - start_tick) < LSM6DSV16X_RESET_TIMEOUT_MS);

  return HAL_TIMEOUT;
}

static HAL_StatusTypeDef LSM6DSV16X_FindDevice(void)
{
  static const uint8_t addresses[] = {
    LSM6DSV16X_I2C_ADDRESS_LOW,
    LSM6DSV16X_I2C_ADDRESS_HIGH
  };
  uint8_t who_am_i;
  uint32_t index;

  for (index = 0U; index < (sizeof(addresses) / sizeof(addresses[0])); ++index)
  {
    lsm6dsv16x_device_address = (uint16_t)(addresses[index] << 1U);
    if ((HAL_I2C_IsDeviceReady(lsm6dsv16x_i2c, lsm6dsv16x_device_address,
                               2U, LSM6DSV16X_I2C_TIMEOUT_MS) == HAL_OK) &&
        (LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_WHO_AM_I, &who_am_i, 1U) == HAL_OK) &&
        (who_am_i == LSM6DSV16X_WHO_AM_I_VALUE))
    {
      return HAL_OK;
    }
  }

  lsm6dsv16x_device_address = 0U;
  return HAL_ERROR;
}

HAL_StatusTypeDef LSM6DSV16X_Init(I2C_HandleTypeDef *hi2c)
{
  uint8_t value;

  if (hi2c == NULL)
  {
    return HAL_ERROR;
  }

  lsm6dsv16x_i2c = hi2c;
  lsm6dsv16x_device_address = 0U;

  if (LSM6DSV16X_FindDevice() != HAL_OK)
  {
    return HAL_ERROR;
  }

  /* Software reset, then wait for the self-clearing bit. */
  if ((LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL3,
                                LSM6DSV16X_CTRL3_SW_RESET) != HAL_OK) ||
      (LSM6DSV16X_WaitWhileSet(LSM6DSV16X_REG_CTRL3,
                               LSM6DSV16X_CTRL3_SW_RESET) != HAL_OK))
  {
    return HAL_ERROR;
  }

  /* Reload the factory calibration values, then restore BDU and IF_INC. */
  if ((LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL3,
                                LSM6DSV16X_CTRL3_BOOT) != HAL_OK) ||
      (LSM6DSV16X_WaitWhileSet(LSM6DSV16X_REG_CTRL3,
                               LSM6DSV16X_CTRL3_BOOT) != HAL_OK) ||
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL3,
                                LSM6DSV16X_CTRL3_BDU |
                                LSM6DSV16X_CTRL3_IF_INC) != HAL_OK))
  {
    return HAL_ERROR;
  }

  /* CTRL1/CTRL2: HP mode (000), ODR = 240 Hz (0111). */
  if ((LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL1, 0x07U) != HAL_OK) ||
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL2, 0x07U) != HAL_OK) ||
      /* CTRL6: gyro full scale = +/-250 dps (FS_G = 001, 8.75 mdps/LSB).
         Whatever this is set to, LSM6DSV16X_GyroRawToMdps must match it. */
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL6, 0x01U) != HAL_OK) ||
      /* CTRL7 bit 0: enable the gyroscope LPF1. */
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL7, 0x01U) != HAL_OK) ||
      /* CTRL8: accelerometer full scale = +/-2 g. */
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL8, 0x00U) != HAL_OK) ||
      /* CTRL9 bit 3: enable the accelerometer LPF2. */
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_CTRL9, 0x08U) != HAL_OK))
  {
    return HAL_ERROR;
  }

  /* Verify the identity once more after configuration. */
  if ((LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_WHO_AM_I, &value, 1U) != HAL_OK) ||
      (value != LSM6DSV16X_WHO_AM_I_VALUE))
  {
    return HAL_ERROR;
  }

  return HAL_OK;
}

HAL_StatusTypeDef LSM6DSV16X_ReadRaw(LSM6DSV16X_RawData *data)
{
  uint8_t status;
  uint8_t buffer[12];

  if (data == NULL)
  {
    return HAL_ERROR;
  }

  if (LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_STATUS, &status, 1U) != HAL_OK)
  {
    return HAL_ERROR;
  }
  if ((status & (LSM6DSV16X_STATUS_XLDA | LSM6DSV16X_STATUS_GDA)) !=
      (LSM6DSV16X_STATUS_XLDA | LSM6DSV16X_STATUS_GDA))
  {
    return HAL_BUSY;
  }

  if (LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_OUTX_L_G, buffer,
                              sizeof(buffer)) != HAL_OK)
  {
    return HAL_ERROR;
  }

  data->gyro_x = (int16_t)(((uint16_t)buffer[1] << 8U) | buffer[0]);
  data->gyro_y = (int16_t)(((uint16_t)buffer[3] << 8U) | buffer[2]);
  data->gyro_z = (int16_t)(((uint16_t)buffer[5] << 8U) | buffer[4]);
  data->accel_x = (int16_t)(((uint16_t)buffer[7] << 8U) | buffer[6]);
  data->accel_y = (int16_t)(((uint16_t)buffer[9] << 8U) | buffer[8]);
  data->accel_z = (int16_t)(((uint16_t)buffer[11] << 8U) | buffer[10]);

  return HAL_OK;
}

uint8_t LSM6DSV16X_GetAddress(void)
{
  return (uint8_t)(lsm6dsv16x_device_address >> 1U);
}

int32_t LSM6DSV16X_AccelRawToMg(int16_t raw)
{
  /* Datasheet sensitivity at +/-2 g: 0.061 mg/LSB. */
  return ((int32_t)raw * 61L) / 1000L;
}

int32_t LSM6DSV16X_GyroRawToMdps(int16_t raw)
{
  /* Datasheet sensitivity at +/-250 dps: 8.75 mdps/LSB, kept as an exact fraction
     so the integer math does not round away the extra resolution. */
  return ((int32_t)raw * 875L) / 100L;
}

/* IEEE 754 binary16 -> binary32. The SFLP block ships quaternion components in
   half precision, so this is the one place the format is decoded. */
static float LSM6DSV16X_HalfToFloat(uint16_t bits)
{
  uint32_t sign = (uint32_t)(bits & 0x8000U) << 16U;
  uint32_t exponent = (uint32_t)(bits >> 10U) & 0x1FU;
  uint32_t mantissa = (uint32_t)bits & 0x03FFU;
  uint32_t single;
  float value;

  if (exponent == 0U)
  {
    if (mantissa == 0U)
    {
      single = sign;                       /* +/- zero */
    }
    else
    {
      /* Subnormal: shift the leading one up into the implicit-bit position,
         counting how far the exponent has to drop. */
      uint32_t shift = 0U;

      mantissa <<= 1U;
      while ((mantissa & 0x0400U) == 0U)
      {
        mantissa <<= 1U;
        ++shift;
      }
      single = sign | ((112U - shift) << 23U) | ((mantissa & 0x03FFU) << 13U);
    }
  }
  else if (exponent == 0x1FU)
  {
    single = sign | 0x7F800000U | (mantissa << 13U);   /* inf / NaN */
  }
  else
  {
    single = sign | ((exponent + 112U) << 23U) | (mantissa << 13U);
  }

  memcpy(&value, &single, sizeof(value));
  return value;
}

HAL_StatusTypeDef LSM6DSV16X_EnableSflp(void)
{
  uint8_t value;

  if ((lsm6dsv16x_i2c == NULL) || (lsm6dsv16x_device_address == 0U))
  {
    return HAL_ERROR;
  }

  if (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_FUNC_CFG_ACCESS,
                               LSM6DSV16X_FUNC_CFG_EMB_ACCESS) != HAL_OK)
  {
    return HAL_ERROR;
  }

  if ((LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_SFLP_ODR,
                                LSM6DSV16X_SFLP_ODR_120HZ) != HAL_OK) ||
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_EMB_FUNC_EN_A,
                                LSM6DSV16X_EMB_FUNC_EN_A_SFLP_GAME) != HAL_OK) ||
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_EMB_FUNC_FIFO_EN_A,
                                LSM6DSV16X_EMB_FUNC_FIFO_EN_A_SFLP_GAME) != HAL_OK))
  {
    (void)LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_FUNC_CFG_ACCESS, 0x00U);
    return HAL_ERROR;
  }

  /* Read the enable bit back with the gate still open. A write that did not
     land, or a part that does not fuse, is worth knowing now rather than from
     an empty FIFO a second later. */
  if ((LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_EMB_FUNC_EN_A, &value, 1U) != HAL_OK) ||
      ((value & LSM6DSV16X_EMB_FUNC_EN_A_SFLP_GAME) == 0U))
  {
    (void)LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_FUNC_CFG_ACCESS, 0x00U);
    return HAL_ERROR;
  }

  if (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_FUNC_CFG_ACCESS, 0x00U) != HAL_OK)
  {
    return HAL_ERROR;
  }

  /* Only the fused word goes into the FIFO. Raw accel/gyro stay unbuffered and
     are read from OUTX_L_G as before, which keeps the FIFO at one word per
     fused sample and the newest quaternion never more than one poll old. */
  if ((LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_FIFO_CTRL3, 0x00U) != HAL_OK) ||
      (LSM6DSV16X_WriteRegister(LSM6DSV16X_REG_FIFO_CTRL4,
                                LSM6DSV16X_FIFO_MODE_STREAM) != HAL_OK))
  {
    return HAL_ERROR;
  }

  return HAL_OK;
}

HAL_StatusTypeDef LSM6DSV16X_ReadSflp(LSM6DSV16X_Sflp *out)
{
  uint8_t fifo_status[2];
  uint8_t buffer[LSM6DSV16X_FIFO_MAX_WORDS * LSM6DSV16X_FIFO_WORD_LEN];
  uint16_t count;
  uint8_t index;

  if (out == NULL)
  {
    return HAL_ERROR;
  }
  out->tag = 0U;
  out->words = 0U;
  out->quat.w = 1.0f;
  out->quat.x = 0.0f;
  out->quat.y = 0.0f;
  out->quat.z = 0.0f;

  /* FIFO_STATUS1 is DIFF_FIFO[7:0], FIFO_STATUS2 bits [2:0] are DIFF_FIFO[10:8].
     They are adjacent, so one transaction answers "is there anything". */
  if (LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_FIFO_STATUS1, fifo_status, 2U) != HAL_OK)
  {
    return HAL_ERROR;
  }

  count = (uint16_t)fifo_status[0] |
          (uint16_t)(((uint16_t)fifo_status[1] & 0x07U) << 8U);
  if (count == 0U)
  {
    return HAL_BUSY;
  }

  out->words = (count > LSM6DSV16X_FIFO_MAX_WORDS)
                 ? (uint8_t)LSM6DSV16X_FIFO_MAX_WORDS
                 : (uint8_t)count;

  if (LSM6DSV16X_ReadRegister(LSM6DSV16X_REG_FIFO_DATA_OUT_TAG, buffer,
                              (uint16_t)((uint16_t)out->words *
                                         LSM6DSV16X_FIFO_WORD_LEN)) != HAL_OK)
  {
    return HAL_ERROR;
  }

  for (index = 0U; index < out->words; ++index)
  {
    const uint8_t *word = &buffer[(uint16_t)index * LSM6DSV16X_FIFO_WORD_LEN];
    /* The tag sits in bits [7:3] of the tag byte; the low bits are reserved. */
    uint8_t tag = (uint8_t)(word[0] >> 3U);

    out->tag = tag;
    if (tag == LSM6DSV16X_FIFO_TAG_SFLP_GAME)
    {
      /* All-zero payload means the fusion block has not written its state yet
         -- an identity quaternion here would report a robot lying on its side
         as upright, so treat it as "no sample" and keep looking. */
      if (((uint16_t)word[1] | (uint16_t)word[2] | (uint16_t)word[3] |
           (uint16_t)word[4] | (uint16_t)word[5] | (uint16_t)word[6]) == 0U)
      {
        continue;
      }

      {
        float x = LSM6DSV16X_HalfToFloat((uint16_t)word[1] |
                                         ((uint16_t)word[2] << 8U));
        float y = LSM6DSV16X_HalfToFloat((uint16_t)word[3] |
                                         ((uint16_t)word[4] << 8U));
        float z = LSM6DSV16X_HalfToFloat((uint16_t)word[5] |
                                         ((uint16_t)word[6] << 8U));
        float norm_sq = (x * x) + (y * y) + (z * z);

        /* x/y/z must lie inside the unit ball, w is the missing fourth
           component. Half-precision rounding can push a full-scale component
           just past it, which is a scale error rather than bad data. */
        if (norm_sq > 1.0f)
        {
          float norm = sqrtf(norm_sq);

          x /= norm;
          y /= norm;
          z /= norm;
          out->quat.w = 0.0f;
        }
        else
        {
          out->quat.w = sqrtf(1.0f - norm_sq);
        }
        out->quat.x = x;
        out->quat.y = y;
        out->quat.z = z;
        return HAL_OK;
      }
    }
  }

  /* Words were waiting, but none of them was a fused quaternion. */
  return HAL_BUSY;
}
