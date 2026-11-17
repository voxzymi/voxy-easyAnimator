#!/usr/bin/env python3
"""Stand-in for upscayl-ncnn used by the tests. Understands the same flags (checked against the
real tool's usage text) and upscales with bicubic. Behaviour is steered by environment variables:

  FAKE_UPSCAYL_OOM_ABOVE=<tile>   fail with an out-of-memory message when -t is 0 or larger than this
  FAKE_UPSCAYL_SLEEP=<seconds>    sleep before working (for timeout / cancel tests)
  FAKE_UPSCAYL_FAIL=1             always fail with a generic error
  FAKE_UPSCAYL_LOG=<file>         append every command line here
"""
import os
import sys
import time

HELP = """Usage: upscayl-bin -i infile -o outfile [options]...

  -h                   show this help
  -i input-path        input image path (jpg/png/webp) or directory
  -o output-path       output image path (jpg/png/webp) or directory
  -z model-scale       scale according to the model (can be 2, 3, 4. default=4)
  -s output-scale      custom output scale (can be 2, 3, 4. default=4)
  -r resize            resize output to dimension (default=WxH:default)
  -w width             resize output to a width (default=W:default)
  -c compress          compression of the output image, default 0 and varies to 100
  -t tile-size         tile size (>=32/0=auto, default=0) can be 0,0,0 for multi-gpu
  -m model-path        folder path to the pre-trained models. default=models
  -n model-name        model name
  -g gpu-id            gpu device to use (default=auto) can be 0,1,2 for multi-gpu
  -j load:proc:save    thread count for load/proc/save (default=1:2:2) can be 1:2,2,2:2 for multi-gpu
  -x                   enable tta mode
  -f format            output image format (jpg/png/webp, default=ext/png)
  -v                   verbose output
"""


def main(argv):
    if "-h" in argv:
        sys.stderr.write(HELP)
        return 0
    args = {}
    i = 0
    while i < len(argv):
        if argv[i].startswith("-") and len(argv[i]) == 2 and argv[i][1] in "iozsrwctmngjf" and i + 1 < len(argv):
            args[argv[i][1]] = argv[i + 1]
            i += 2
        else:
            i += 1
    log = os.environ.get("FAKE_UPSCAYL_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(argv) + "\n")
    time.sleep(float(os.environ.get("FAKE_UPSCAYL_SLEEP", "0")))
    if os.environ.get("FAKE_UPSCAYL_FAIL"):
        sys.stderr.write("something unexpected happened\n")
        return 3
    limit = os.environ.get("FAKE_UPSCAYL_OOM_ABOVE")
    tile = int(args.get("t", "0"))
    if limit is not None and (tile == 0 or tile > int(limit)):
        sys.stderr.write("vkAllocateMemory failed -1000069000\nvkQueueSubmit failed -4\n")
        return 1
    from PIL import Image
    im = Image.open(args["i"]).convert("RGBA")
    s = int(args.get("s", "4"))
    im.resize((im.width * s, im.height * s), Image.BICUBIC).save(args["o"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
