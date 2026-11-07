#!/usr/bin/env python3
"""Read and write the Canon MC-16 maintenance cartridge chip from a Raspberry Pi.

Runs on the Pi with sudo. Bit-bangs I2C on GPIO2 and GPIO3 and works out which
line is SDA, so a rewired harness still works. Chip: 256 bytes at I2C 0x51.

  mc16chip.py probe            exit 0 if the chip answers, 2 if not
  mc16chip.py read OUT.bin     read twice, save only if both reads match
  mc16chip.py write IMAGE.bin  write changed bytes, then read back and compare
"""
import subprocess
import sys
import time

import lgpio

ADDR = 0x51
SIZE = 256
D = 0.0001
H = lgpio.gpiochip_open(0)


class NoChip(Exception):
    pass


class Bus:
    def __init__(self, sda, scl):
        self.sda, self.scl = sda, scl

    @staticmethod
    def rel(pin):
        lgpio.gpio_claim_input(H, pin, lgpio.SET_PULL_UP)

    @staticmethod
    def low(pin):
        lgpio.gpio_claim_output(H, pin, 0)

    def clear(self):
        self.rel(self.sda)
        for _ in range(9):
            self.low(self.scl); time.sleep(D); self.rel(self.scl); time.sleep(D)
        self.stop()

    def start(self):
        self.rel(self.sda); time.sleep(D); self.rel(self.scl); time.sleep(D)
        self.low(self.sda); time.sleep(D); self.low(self.scl); time.sleep(D)

    def stop(self):
        self.low(self.sda); time.sleep(D); self.rel(self.scl); time.sleep(D)
        self.rel(self.sda); time.sleep(D)

    def wbyte(self, b):
        for i in range(7, -1, -1):
            (self.rel if (b >> i) & 1 else self.low)(self.sda); time.sleep(D)
            self.rel(self.scl); time.sleep(D); self.low(self.scl); time.sleep(D)
        self.rel(self.sda); time.sleep(D); self.rel(self.scl); time.sleep(D)
        ack = lgpio.gpio_read(H, self.sda) == 0
        self.low(self.scl); time.sleep(D)
        return ack

    def rbyte(self, ack):
        self.rel(self.sda)
        v = 0
        for _ in range(8):
            time.sleep(D); self.rel(self.scl); time.sleep(D)
            v = v * 2 + lgpio.gpio_read(H, self.sda)
            self.low(self.scl)
        time.sleep(D)
        (self.low if ack else self.rel)(self.sda); time.sleep(D)
        self.rel(self.scl); time.sleep(D); self.low(self.scl); time.sleep(D)
        self.rel(self.sda)
        return v

    def answers(self):
        self.clear()
        self.start()
        ok = self.wbyte(ADDR << 1)
        self.stop()
        return ok

    def dump(self):
        self.start()
        if not self.wbyte(ADDR << 1) or not self.wbyte(0):
            self.stop()
            raise NoChip("no ack on read setup")
        self.start()
        if not self.wbyte(ADDR << 1 | 1):
            self.stop()
            raise NoChip("no ack on read")
        out = bytes(self.rbyte(i < SIZE - 1) for i in range(SIZE))
        self.stop()
        return out

    def write_byte(self, offset, value):
        self.start()
        ok = self.wbyte(ADDR << 1) and self.wbyte(offset) and self.wbyte(value)
        self.stop()
        if not ok:
            raise NoChip(f"no ack writing {offset:#x}")
        for _ in range(100):
            self.start()
            ready = self.wbyte(ADDR << 1)
            self.stop()
            if ready:
                return
            time.sleep(0.001)
        raise NoChip("chip stayed busy after a write")


def find_bus():
    for sda, scl in ((3, 2), (2, 3)):
        bus = Bus(sda, scl)
        if bus.answers():
            print(f"chip answers at {ADDR:#x}, SDA=GPIO{sda}, SCL=GPIO{scl}")
            return bus
    raise NoChip("chip does not answer on either line order")


def stable_read(bus):
    first = bus.dump()
    if first != bus.dump():
        raise NoChip("two reads differ, contact is unstable")
    return first


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("probe", "read", "write"):
        sys.exit(__doc__)
    cmd = sys.argv[1]
    bus = find_bus()
    if cmd == "read":
        data = stable_read(bus)
        with open(sys.argv[2], "wb") as f:
            f.write(data)
        print(f"saved {len(data)} bytes to {sys.argv[2]}")
    elif cmd == "write":
        with open(sys.argv[2], "rb") as f:
            want = f.read()
        if len(want) != SIZE:
            sys.exit(f"image must be {SIZE} bytes, got {len(want)}")
        current = stable_read(bus)
        changes = [i for i in range(SIZE) if current[i] != want[i]]
        print("bytes to change:", len(changes))
        for i in changes:
            bus.write_byte(i, want[i])
        if stable_read(bus) != want:
            raise NoChip("read-back does not match the image")
        print("write verified")


if __name__ == "__main__":
    code = 0
    try:
        main()
    except NoChip as e:
        print(e)
        code = 2
    finally:
        lgpio.gpiochip_close(H)
        subprocess.run(["pinctrl", "set", "2,3", "a0", "pu"], check=False)
    sys.exit(code)
