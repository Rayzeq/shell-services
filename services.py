# ruff: noqa: D100, D101, D102, D103, S602, S603, S607

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace

DIRENV_DIR = os.environ.get("DIRENV_DIR")
CWD = Path.cwd() if DIRENV_DIR is None else Path(DIRENV_DIR.removeprefix("-"))
SAFE_PATH = str(CWD).replace("/", "-")


@dataclass
class Service:
    name: str
    env: dict[str, str]
    start_cmd: list[str]
    check_cmd: str | None
    working_directory: str | None

    @property
    def systemd_name(self) -> str:
        return f"direnv{SAFE_PATH}-{self.name}.service"

    def check(self) -> bool:
        if self.check_cmd:
            process = subprocess.run(self.check_cmd, shell=True, check=False)
        else:
            process = subprocess.run(
                ["systemctl", "--user", "-q", "is-active", self.systemd_name],
                check=False,
            )

        return process.returncode == 0

    def start(self) -> None:
        if self.check():
            log(f"{self.name} is already running")
            return

        subprocess.run(
            ["systemctl", "--user", "-q", "reset-failed", self.systemd_name],
            # prevent error from being displayed if reset wasn't needed
            capture_output=True,
            check=False,
        )

        working_directory: Path = (
            CWD
            if self.working_directory is None
            else (
                Path(self.working_directory)
                if Path(self.working_directory).is_absolute()
                else (CWD / self.working_directory).resolve()
            )
        )
        process = subprocess.run(
            [
                "systemd-run",
                "--user",
                "-q",
                "--working-directory",
                str(working_directory),
                "-u",
                self.systemd_name,
                "--setenv",
                f"PWD={CWD}",
                *(
                    x
                    for name, value in self.env.items()
                    for x in ("--setenv", f"{name}={value}")
                ),
                *self.start_cmd,
            ],
            check=False,
        )

        if process.returncode != 0:
            error(f"{self.name} failed to start")
            return

        time.sleep(0.2)

        if self.check():
            log(f"{self.name} started")
        else:
            error(f"{self.name} failed to start")

    def stop(self) -> None:
        if not self.check():
            log(f"{self.name} isn't running")
            return

        process = subprocess.run(
            ["systemctl", "--user", "-q", "stop", self.systemd_name],
            check=False,
        )
        if process.returncode != 0:
            error(f"{self.name} failed to stop")
            return

        time.sleep(0.2)

        if not self.check():
            log(f"{self.name} stopped")
        else:
            error(f"{self.name} failed to stop")

    def state(self) -> str:
        process = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "-p",
                "SubState",
                "--value",
                self.systemd_name,
            ],
            capture_output=True,
        )
        return process.stdout.decode()

    def status(self) -> None:
        subprocess.run(
            ["systemctl", "--user", "status", self.systemd_name],
            check=False,
        )


class Watchdog:
    socket_path: Path = Path(
        f"/run/user/{os.getuid()}/direnv-watchdog{SAFE_PATH}.sock",
    )
    systemd_unit: str = f"direnv{SAFE_PATH}-watchdog.service"

    @classmethod
    def start(cls) -> None:
        if cls.socket_path.exists():
            cls.socket_path.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(cls.socket_path))
        server.settimeout(2.0)

        pids = set()
        base_path = CWD.resolve()

        while True:
            try:
                data = server.recv(1024)
                if (pid := data.decode().strip()).isdigit():
                    pids.add(int(pid))
                    log(f"Adding {pid} to watchlist")
            except TimeoutError:
                pass

            if not pids:
                continue

            active = False
            dead_pids = set()

            for pid in pids:
                cwd_path = Path(f"/proc/{pid}/cwd")
                if cwd_path.resolve(strict=False).is_relative_to(base_path):
                    active = True
                else:
                    dead_pids.add(pid)
                    log(f"{pid} is dead or left directory, removing from watchlist")

            pids -= dead_pids

            if not pids and not active:
                for service in SERVICES.values():
                    service.stop()

                cls.socket_path.unlink()
                break

    @classmethod
    def stop(cls) -> None:
        subprocess.run(
            [
                "systemctl",
                "--user",
                "-q",
                "stop",
                cls.systemd_unit,
            ],
            # systemd will only return an error if it can't find the unit,
            # meaning the watchdog is already not running.
            # ignoring the return code and preventing the error message from
            # being visible
            check=False,
            capture_output=True,
        )

    @classmethod
    def register(cls, pid: int) -> None:
        process = subprocess.run(
            ["systemctl", "--user", "-q", "is-active", cls.systemd_unit],
            check=False,
        )
        if process.returncode != 0:
            subprocess.run(
                [
                    "systemd-run",
                    "--user",
                    "-q",
                    "-d",
                    "-u",
                    cls.systemd_unit,
                    "--setenv=SERVICES=" + os.environ["SERVICES"],
                    "--setenv=PYTHONUNBUFFERED=1",
                    sys.executable,
                    Path(__file__).resolve(),
                    "watchdog",
                ],
                check=True,
            )

        # if we're in direnv's subshell, get the parent shell
        try:
            ppid = int(
                Path(f"/proc/{pid}/stat").read_text().split(") ")[1].split(" ")[1],
            )
            parent_name = Path(f"/proc/{ppid}/comm").read_text().strip()
            if parent_name == "direnv":
                pid = int(
                    Path(f"/proc/{ppid}/stat").read_text().split(") ")[1].split(" ")[1],
                )

                this_name = Path(f"/proc/{pid}/comm").read_text().strip()
                ppid = int(
                    Path(f"/proc/{pid}/stat").read_text().split(") ")[1].split(" ")[1],
                )
                parent_name = Path(f"/proc/{ppid}/comm").read_text().strip()
                # we're likely in the zsh start phase,
                # meaning the current shell is actually a subshell of the real one
                if this_name == "zsh" and parent_name == "zsh":
                    pid = ppid
        except OSError:
            pass

        # basic retry logic to let watchdog start
        for _ in range(20):
            if cls.socket_path.exists():
                try:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                    client.sendto(str(pid).encode(), str(cls.socket_path))
                    break
                except OSError:
                    pass
            time.sleep(0.1)


SERVICES: dict[str, Service] = {
    name: Service(
        name,
        service.get("env", {}),
        service["start"],
        service.get("check"),
        service.get("working-directory"),
    )
    for name, service in json.loads(os.environ["SERVICES"]).items()
}
COLOR = ""
PREFIX = ""


def log(text: str) -> None:
    print(f"{COLOR}{PREFIX}{text}\x1b[0m")


def error(text: str) -> None:
    print(f"\x1b[31m{PREFIX}{text}\x1b[0m")


def parse_arguments() -> Namespace:
    parser = argparse.ArgumentParser(prog="services")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="hint that the command was ran automatically",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="shell pid to register with watchdog",
    )

    subparsers = parser.add_subparsers(required=True, dest="command")

    parser_start = subparsers.add_parser("start", help="start services")
    parser_start.add_argument(
        "name",
        choices=SERVICES.keys(),
        nargs="?",
        help="the service to start",
    )

    parser_stop = subparsers.add_parser("stop", help="stop services")
    parser_stop.add_argument(
        "name",
        choices=SERVICES.keys(),
        nargs="?",
        help="the service to stop",
    )

    parser_status = subparsers.add_parser("status", help="get services status")
    parser_status.add_argument(
        "name",
        choices=SERVICES.keys(),
        nargs="?",
        help="the service to get status for",
    )

    subparsers.add_parser("watchdog", help=argparse.SUPPRESS)

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.command == "watchdog":
        Watchdog.start()
        return

    if args.auto:
        global PREFIX
        global COLOR
        COLOR = "\x1b[2m"
        PREFIX = "services: "

    if args.command == "start":
        if args.name is None:
            for service in SERVICES.values():
                service.start()
        else:
            SERVICES[args.name].start()

        if args.auto and args.pid:
            Watchdog.register(args.pid)
    elif args.command == "stop":
        if args.name is None:
            for service in SERVICES.values():
                service.stop()

            Watchdog.stop()
        else:
            SERVICES[args.name].stop()
    elif args.command == "status":
        if args.name is None:
            for name, service in SERVICES.items():
                log(f"{name}: {service.state()}")
        else:
            SERVICES[args.name].status()
    else:
        raise RuntimeError


if __name__ == "__main__":
    main()
