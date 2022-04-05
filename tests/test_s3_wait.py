#!/usr/bin/env python3

import asyncio
from xpulumi.s3_object_waiter import async_wait_and_get_s3_object

async def main():
  result = await async_wait_and_get_s3_object(
      uri="s3://492598163938-us-west-2-aws-env-dev/projects/g/dev-box/dev/cloud-init-status.json",
      region_name='us-west-2',
      max_wait_seconds=30,
      poll_interval=4,
    )
  print(f"Result: [{result.decode('utf-8')}]")

asyncio.run(main())