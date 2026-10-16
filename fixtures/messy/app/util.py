import os
import sys
import subprocess
import yaml


def used(x):
    return x + 1


def never_called(x):
    return x * 2


def tangled(a, b, c, d):
    if a:
        if b:
            for i in range(10):
                if i % 2 == 0 and c:
                    return 1
                elif i % 3 == 0 or d:
                    return 2
        elif c:
            while d:
                if a and b:
                    return 3
                d -= 1
    elif b:
        try:
            return int(c)
        except ValueError:
            return 4
    else:
        for j in range(3):
            if j == a or j == b:
                return 5
    return 0


def run(cmd):
    return subprocess.call(cmd, shell=True)


def main():
    print(used(1), tangled(1, 2, 3, 4), yaml)
