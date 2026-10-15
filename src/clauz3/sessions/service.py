"""Private child-process entry point for the launcher's approval service."""

import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

from clauz3.approvals.authority import ApprovedLayer
from clauz3.approvals.client import APPROVAL_DECISION_TOKEN_ENV, APPROVAL_TOKEN_ENV
from clauz3.approvals.policy import load_approval_policy
from clauz3.approvals.profiles.store import ProfileStore
from clauz3.approvals.service import create_approval_app
from clauz3.libraries.discovery import global_library_root
from clauz3.libraries.layout import expand_import_roots
from clauz3.sessions.chat import ChatConfig


def main() -> None:
    fd, cwd, roots, *policy = sys.argv[1:]
    ui_config = os.environ.pop("CLAUZ3_UI_CONFIG", None)
    app = create_approval_app(
        auth_token=os.environ[APPROVAL_TOKEN_ENV],
        decision_token=os.environ[APPROVAL_DECISION_TOKEN_ENV],
        allowed_hosts=["127.0.0.1"],
        authorized_layers=(
            ApprovedLayer(
                trusted_roots=tuple(Path(root) for root in json.loads(roots)),
                import_roots=tuple(expand_import_roots([Path(cwd)])),
            ),
        ),
        policy=load_approval_policy(Path(policy[0])) if policy else None,
        profile=(
            ProfileStore(
                global_library_root() / "approval-history.sqlite", project=Path(cwd)
            )
            if not policy
            else None
        ),
        chat_config=(
            ChatConfig.model_validate_json(Path(ui_config).read_text()).model_copy(
                update={
                    "history_file": str(global_library_root() / "conversations.sqlite")
                }
            )
            if ui_config
            else None
        ),
    )
    # The parent binds once and passes the socket: no free-port selection race.
    with socket.socket(fileno=int(fd)) as listener:
        uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="warning",
                access_log=False,
            )
        ).run(sockets=[listener])


if __name__ == "__main__":
    main()
