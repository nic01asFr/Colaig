# SPDX-License-Identifier: MIT
"""
Legacy Grist API client - extracted from iam.py.

Only imported if Grist credentials are configured.
Will be removed once the Platform Registry fully replaces Grist.
"""

import json
from collections import namedtuple

import aiohttp

UserRecord = namedtuple(
    "UserRecord",
    ["id", "tchap_user", "status", "domain", "n_questions", "last_activity"],
    defaults=(None,),
)


def to_record(_id: str, data: dict) -> UserRecord:
    """Dict to a Grist record."""
    return UserRecord(**{"id": _id, **{k: v for k, v in data.items() if k in UserRecord._fields}})


class AsyncGristDocAPI:
    def __init__(self, doc_id: str, server: str, api_key: str):
        self.doc_id = doc_id
        self.server = server
        self.api_key = api_key
        self.base_url = f"{server}/api"

    async def _request(self, method, endpoint, json_data=None):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with aiohttp.ClientSession() as session:
            if method in ["GET"]:
                data = {"params": json_data}
            else:
                headers["Content-Type"] = "application/json"
                data = {"json": json_data}

            async with session.request(
                method, self.base_url + endpoint, headers=headers, **data
            ) as response:
                response.raise_for_status()
                return await response.json()

    async def fetch_table(self, table_id, filters=None) -> list[UserRecord]:
        endpoint = f"/docs/{self.doc_id}/tables/{table_id}/records"
        data = {}
        if filters:
            data["filter"] = json.dumps(filters)
        result = await self._request("GET", endpoint, data)

        if not result["records"]:
            return []
        return [to_record(r["id"], r["fields"]) for r in result["records"]]

    async def add_records(self, table_id, records):
        endpoint = f"/docs/{self.doc_id}/tables/{table_id}/records"
        data = {"records": [{"fields": r} for r in records]}
        return await self._request("POST", endpoint, data)

    async def update_records(self, table_id, records):
        endpoint = f"/docs/{self.doc_id}/tables/{table_id}/records"
        records = [r.copy() for r in records]
        data = {"records": [{"id": r.pop("id"), "fields": r} for r in records]}
        return await self._request("PATCH", endpoint, data)
