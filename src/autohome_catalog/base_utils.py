"""Shared Base reads, bounded writes and read-back checks; no migration entry points."""

import time

from .feishu import record_index, same_value


def set_visible(client, args, visible):
    visible = list(dict.fromkeys(visible))
    client.call("+view-set-visible-fields", args, body={"visible_fields": visible})
    for attempt in range(5):
        actual = client.call("+view-get-visible-fields", args)["visible_fields"]
        if actual == visible:
            return actual
        time.sleep(0.5 * (attempt + 1))
    raise ValueError("Visible parameter order differs after bounded read-back")


def read_records(client, table_id, key):
    for attempt in range(4):
        try:
            return _read_records_once(client, table_id, key)
        except ValueError as error:
            if str(error) != "Table changed during layout read-back" or attempt == 3:
                raise
            time.sleep(min(2**attempt, 4))


def _read_records_once(client, table_id, key):
    rows, offset, revision = [], 0, None
    while True:
        data = client.call(
            "+record-list", ["--table-id", table_id, "--limit", "200", "--offset", str(offset)]
        )
        if revision is not None and revision != data["rev"]:
            raise ValueError("Table changed during layout read-back")
        revision = data["rev"]
        rows.extend(
            {"record_id": rid, "fields": dict(zip(data["fields"], values, strict=True))}
            for rid, values in zip(data["record_id_list"], data["data"], strict=True)
        )
        if not data["has_more"]:
            record_index(rows, key)
            return rows
        if not data["data"]:
            raise ValueError("Empty page before end")
        offset += len(data["data"])


def write_updates(client, tid, updates):
    items = list(updates.items())
    for start in range(0, len(items), 200):
        client.call(
            "+record-batch-update",
            ["--table-id", tid],
            {"update_records": dict(items[start : start + 200])},
        )


def verify_fields(expected, actual):
    from .image_sync import url_lines

    for name, value in expected.items():
        if value is None and actual.get(name) in (None, ""):
            continue
        if isinstance(value, list) and not value and actual.get(name) in (None, []):
            continue
        if type(value) is bool or value is None:
            if actual.get(name) is not value:
                raise ValueError(f"Typed cell differs: {name}")
        elif (
            name == "图片URL清单"
            and isinstance(value, str)
            and isinstance(actual.get(name), str)
            and url_lines(value) == url_lines(actual[name])
        ):
            continue
        elif not same_value(value, actual.get(name)):
            raise ValueError(f"Cell differs: {name}")


def settled_fields(client, tid, predicate):
    for attempt in range(6):
        fields = client.call("+field-list", ["--table-id", tid])["fields"]
        if predicate(fields):
            return fields
        time.sleep(1 + attempt)
    raise ValueError("Field definition has not propagated; resume this run later")
