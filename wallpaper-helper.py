#!/usr/bin/env python3
"""Safely inspect and update macOS Tahoe aerial wallpaper choices."""

import argparse
import fcntl
import json
import os
import plistlib
import shutil
import stat
import sys
import tempfile


AERIAL_PROVIDER = "com.apple.wallpaper.choice.aerials"
TARGET_KINDS = {"Desktop", "Idle"}


def load_plist(path):
    with open(path, "rb") as handle:
        return plistlib.load(handle)


def configuration_asset_id(choice):
    configuration = choice.get("Configuration")
    if not isinstance(configuration, (bytes, bytearray)):
        return None

    try:
        decoded = plistlib.loads(bytes(configuration))
    except (ValueError, TypeError, plistlib.InvalidFileException):
        return None

    if not isinstance(decoded, dict):
        return None
    asset_id = decoded.get("assetID")
    return asset_id if isinstance(asset_id, str) else None


def collect_target_choices(root):
    """Collect Choices owned specifically by Desktop and Idle nodes."""
    results = []
    seen = set()

    def walk(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                key_string = str(key)
                child_path = path + (key_string,)

                if key_string in TARGET_KINDS and isinstance(value, dict):
                    content = value.get("Content")
                    choices = content.get("Choices") if isinstance(content, dict) else None
                    if isinstance(choices, list):
                        for index, choice in enumerate(choices):
                            if isinstance(choice, dict) and id(choice) not in seen:
                                seen.add(id(choice))
                                results.append(
                                    {
                                        "kind": key_string,
                                        "path": ".".join(child_path + ("Content", "Choices", str(index))),
                                        "choice": choice,
                                    }
                                )

                walk(value, child_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + (str(index),))

    walk(root)
    # Recent macOS releases store the active wallpaper in Linked nodes under
    # AllSpacesAndDisplays and SystemDefault instead of per-Desktop/Idle nodes.
    # Fill whichever target kind is absent, preserving all other plist data.
    if isinstance(root, dict):
        has_desktop = any(item["kind"] == "Desktop" for item in results)
        has_idle = any(item["kind"] == "Idle" for item in results)
        fallback_owners = []
        if not has_desktop:
            fallback_owners.append("AllSpacesAndDisplays")
        if not has_idle:
            fallback_owners.append("SystemDefault")
        for owner in fallback_owners:
            owner_node = root.get(owner)
            linked = owner_node.get("Linked") if isinstance(owner_node, dict) else None
            content = linked.get("Content") if isinstance(linked, dict) else None
            choices = content.get("Choices") if isinstance(content, dict) else None
            if isinstance(choices, list):
                for index, choice in enumerate(choices):
                    if isinstance(choice, dict) and id(choice) not in seen:
                        seen.add(id(choice))
                        results.append({
                            "kind": owner,
                            "path": f"{owner}.Linked.Content.Choices.{index}",
                            "choice": choice,
                        })
    return results


def status_payload(plist_path, desired_asset_id):
    data = load_plist(plist_path)
    targets = collect_target_choices(data)
    choices = [
        {
            "kind": target["kind"],
            "path": target["path"],
            "provider": target["choice"].get("Provider"),
            "assetID": configuration_asset_id(target["choice"]),
        }
        for target in targets
    ]
    desktop_count = sum(choice["kind"] in ("Desktop", "AllSpacesAndDisplays") for choice in choices)
    idle_count = sum(choice["kind"] in ("Idle", "SystemDefault") for choice in choices)
    all_match = (
        desktop_count > 0
        and idle_count > 0
        and all(choice["assetID"] == desired_asset_id for choice in choices)
    )
    return {
        "allMatch": all_match,
        "desktopCount": desktop_count,
        "idleCount": idle_count,
        "choices": choices,
    }


def fsync_file(path):
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def atomic_copy(source, destination):
    destination_directory = os.path.dirname(destination)
    os.makedirs(destination_directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=os.path.basename(destination) + ".",
        suffix=".tmp",
        dir=destination_directory,
    )
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        fsync_file(temporary)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_plist(path, data):
    output = plistlib.dumps(data, fmt=plistlib.FMT_BINARY)
    plistlib.loads(output)

    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(output)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_asset(plist_path, desired_asset_id, last_good_path):
    lock_path = os.path.join(
        os.path.dirname(plist_path),
        ".tahoe-wallpaper-switcher.lock",
    )

    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_plist(plist_path)
        targets = collect_target_choices(data)
        desktop_count = sum(target["kind"] in ("Desktop", "AllSpacesAndDisplays") for target in targets)
        idle_count = sum(target["kind"] in ("Idle", "SystemDefault") for target in targets)
        if desktop_count == 0 or idle_count == 0:
            raise RuntimeError(
                "Expected at least one Desktop and one Idle Choice; "
                f"found Desktop={desktop_count}, Idle={idle_count}"
            )

        atomic_copy(plist_path, last_good_path)

        configuration = plistlib.dumps(
            {"assetID": desired_asset_id},
            fmt=plistlib.FMT_BINARY,
        )
        for target in targets:
            target["choice"]["Provider"] = AERIAL_PROVIDER
            target["choice"]["Configuration"] = configuration

        replaced = False
        try:
            atomic_write_plist(plist_path, data)
            replaced = True
            verification = status_payload(plist_path, desired_asset_id)
            if not verification["allMatch"]:
                raise RuntimeError(
                    "Post-write verification failed: "
                    + json.dumps(verification, ensure_ascii=False)
                )
        except Exception as error:
            if replaced and os.path.isfile(last_good_path):
                atomic_copy(last_good_path, plist_path)
                raise RuntimeError(
                    f"Update failed and last-good settings were restored: {error}"
                ) from error
            raise

        return {
            "updated": len(targets),
            "desktopCount": desktop_count,
            "idleCount": idle_count,
            "assetID": desired_asset_id,
            "lastGood": last_good_path,
        }


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--plist", required=True)
    status_parser.add_argument("--asset-id", required=True)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plist", required=True)
    apply_parser.add_argument("--asset-id", required=True)
    apply_parser.add_argument("--last-good", required=True)

    return parser.parse_args()


def main():
    args = parse_args()
    try:
        if args.command == "status":
            result = status_payload(args.plist, args.asset_id)
        else:
            result = apply_asset(args.plist, args.asset_id, args.last_good)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
