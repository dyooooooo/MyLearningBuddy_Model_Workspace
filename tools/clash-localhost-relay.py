#!/usr/bin/env python3
"""TCP relay: 127.0.0.1:7897 -> Windows Clash (LAN).

Cursor Remote injects http://127.0.0.1:7897 into the Spark extension host.
Clash runs on Windows; this bridge makes that localhost port work.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        remote_reader, remote_writer = await asyncio.open_connection(
            target_host, target_port
        )
    except OSError as exc:
        print(f"connect {target_host}:{target_port} failed: {exc}", file=sys.stderr)
        writer.close()
        await writer.wait_closed()
        return
    await asyncio.gather(
        _pipe(reader, remote_writer),
        _pipe(remote_reader, writer),
        return_exceptions=True,
    )


async def _main(listen_host: str, listen_port: int, target_host: str, target_port: int) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, target_host, target_port),
        listen_host,
        listen_port,
    )
    sockets = ", ".join(str(s.getsockname()) for s in (server.sockets or []))
    print(f"relay listening on {sockets} -> {target_host}:{target_port}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=7897)
    parser.add_argument(
        "--target-host",
        default=os.environ.get("CLASH_HOST", "192.168.1.7"),
    )
    parser.add_argument(
        "--target-port",
        type=int,
        default=int(os.environ.get("CLASH_PORT", "7897")),
    )
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(
            _main(args.listen_host, args.listen_port, args.target_host, args.target_port)
        )
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
