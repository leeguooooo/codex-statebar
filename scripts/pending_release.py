"""A committed version is complete only when its release is public."""
import json
import sys


def pending_tag(version, releases):
    tag = 'v' + version
    return '' if any(r['tagName'] == tag and not r['isDraft']
                     for r in releases) else tag


if __name__ == '__main__':
    print(pending_tag(sys.argv[1], json.load(sys.stdin)))
