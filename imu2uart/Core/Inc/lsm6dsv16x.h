/**
  ******************************************************************************
  * @file    lsm6dsv16x.h
  * @brief   Minimal I2C driver for the ST LSM6DSV16X IMU.
  ******************************************************************************
  */

#ifndef __LSM6DSV16X_H__
#define __LSM6DSV16X_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"

#define LSM6DSV16X_I2C_ADDRESS_LOW       0x6AU
#define LSM6DSV16X_I2C_ADDRESS_HIGH      0x6BU
#define LSM6DSV16X_WHO_AM_I_VALUE        0x70U

typedef struct
{
  int16_t gyro_x;
  int16_t gyro_y;
  int16_t gyro_z;
  int16_t accel_x;
  int16_t accel_y;
  int16_t accel_z;
} LSM6DSV16X_RawData;

/* One SFLP game-rotation-vector sample, scalar-first [w, x, y, z].
   The chip ships x/y/z only; w is rebuilt here from the unit-norm constraint. */
typedef struct
{
  float w;
  float x;
  float y;
  float z;
} LSM6DSV16X_Quat;

/* Outcome of one FIFO drain, diagnostics included: the SFLP block is the only
   thing that puts anything in the FIFO here, so `words` and `tag` say exactly
   why a sample is missing when one is. */
typedef struct
{
  LSM6DSV16X_Quat quat;   /* valid only when LSM6DSV16X_ReadSflp returned HAL_OK */
  uint8_t tag;            /* tag of the last FIFO word consumed, 0 if the FIFO was empty */
  uint8_t words;          /* words the FIFO held (clamped to the drain limit) */
} LSM6DSV16X_Sflp;

HAL_StatusTypeDef LSM6DSV16X_Init(I2C_HandleTypeDef *hi2c);
HAL_StatusTypeDef LSM6DSV16X_ReadRaw(LSM6DSV16X_RawData *data);
uint8_t LSM6DSV16X_GetAddress(void);

/* Turn on the in-chip sensor fusion block and route its game rotation vector
   into the FIFO. Call after LSM6DSV16X_Init. Failure is not fatal: the raw
   accel/gyro path above keeps working and the caller can fall back to it. */
HAL_StatusTypeDef LSM6DSV16X_EnableSflp(void);

/* Non-blocking drain of the FIFO, keeping the newest game rotation vector of
   this poll. HAL_OK = quat filled, HAL_BUSY = no such word this time (check
   tag/words), HAL_ERROR = I2C. */
HAL_StatusTypeDef LSM6DSV16X_ReadSflp(LSM6DSV16X_Sflp *out);

/* Conversion helpers for the configured +/-2 g and +/-250 dps ranges. */
int32_t LSM6DSV16X_AccelRawToMg(int16_t raw);
int32_t LSM6DSV16X_GyroRawToMdps(int16_t raw);

#ifdef __cplusplus
}
#endif

#endif /* __LSM6DSV16X_H__ */
