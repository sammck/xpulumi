#!/usr/bin/env python3


from typing import Optional, Sequence

import sys

from xpulumi.pulumi_cli.wrapper import PulumiWrapper

def run(argv: Optional[Sequence[str]]=None) -> int:
  if argv is None:
    argv = sys.argv[1:]
  rc = PulumiWrapper(argv).call()
  return rc

if __name__ == '__main__':
  sys.exit(run())
