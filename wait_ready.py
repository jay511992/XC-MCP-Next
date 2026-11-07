# -*- coding: utf-8 -*-
"""Wait for one selected IDE: python wait_ready.py [seconds] [--project FULL_PATH]."""
import argparse
import http.client
import json
import math
import os
import sys
import time
import urllib.error

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge"))
import instances


def read_token():
    return instances.read_token()


def wait_for_target(seconds=40, *, project=None, instance=None, url=None,
                    instance_dir=None, poll_interval=0.5):
    """Return a verified Target. Zero means one probe (up to 2s), without retries."""
    if not math.isfinite(seconds) or seconds < 0 or not math.isfinite(poll_interval) or poll_interval <= 0:
        raise instances.ConfigurationError("timeout must be finite/nonnegative and polling interval finite/positive")
    options = instances.selection_options(project=project, instance=instance, url=url,
                                          instance_dir=instance_dir)
    single_probe = seconds == 0
    deadline = time.monotonic() + (2.0 if single_probe else seconds)
    last_error = "no matching target"
    while True:
        try:
            # resolve performs authenticated live health checks with both guards.
            # It returns only after selection succeeds, so no bound target is ever
            # discarded in favour of a different one on a later retry.
            return instances.resolve(**options, use_env=False,
                                     timeout=max(0.01, min(2.0, deadline - time.monotonic())),
                                     deadline=deadline)
        except (instances.ConfigurationError, instances.AmbiguousMatchError, instances.IdentityError):
            raise
        except (instances.NoMatchError, OSError, http.client.HTTPException) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 409:
                raise instances.IdentityError("selected instance/project rejected /health (HTTP 409)") from exc
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if single_probe or remaining <= 0:
            elapsed = "a single probe" if single_probe else "%gs" % seconds
            raise TimeoutError("not ready after %s: %s" % (elapsed, last_error))
        time.sleep(min(poll_interval, remaining))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", nargs="?", type=float, default=40,
                        help="total wait seconds; 0 = one verification probe (up to 2s)")
    instances.add_selection_arguments(parser)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    started = time.monotonic()
    try:
        target = wait_for_target(args.seconds, **instances.arguments_options(args))
        print("ready after %.1fs: %s" %
              (time.monotonic() - started, json.dumps(target.info, ensure_ascii=False)))
        return 0
    except TimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (instances.InstanceError, OSError, http.client.HTTPException) as exc:
        print("not ready: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
