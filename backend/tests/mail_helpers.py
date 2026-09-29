"""邮箱捕获测试共用的 fake IMAP 与邮件构造工具。"""

from __future__ import annotations

from email.message import EmailMessage
from io import BytesIO

from docx import Document

from app.config import Settings
from app.main import create_app

DOCX_SUBTYPE = "vnd.openxmlformats-officedocument.wordprocessingml.document"


class FakeIMAPClient:
    """模拟只存未读邮件的最小 IMAP 服务器，类属性在连接实例间共享。"""

    mailbox: list[tuple[bytes, bytes]] = []
    seen: set[bytes] = set()

    def __init__(self, host, port):
        pass

    def login(self, user, password):
        return ("OK", [b"Logged in"])

    def select(self, mailbox):
        return ("OK", [b"1"])

    def uid(self, command, *args):
        if command == "search":
            unseen = [uid for uid, _raw in self.mailbox if uid not in self.seen]
            return ("OK", [b" ".join(unseen) if unseen else b""])
        if command == "fetch":
            uid = args[0]
            if args[1] == "(RFC822)":
                self.seen.add(uid)
            raw = dict(self.mailbox)[uid]
            return ("OK", [(uid + b" (RFC822 {100}", raw), b")"])
        if command == "store":
            self.seen.add(args[0])
            return ("OK", [b"Store complete"])
        return ("NO", [b"unsupported"])

    def logout(self):
        return ("OK", [b"Bye"])


def install_fake_imap(monkeypatch, messages: list[bytes]) -> None:
    """把 fake IMAP 装进 mail_ingest 模块；messages 为原始邮件字节列表。"""

    FakeIMAPClient.mailbox = [(str(index).encode(), raw) for index, raw in enumerate(messages, 1)]
    FakeIMAPClient.seen = set()
    monkeypatch.setattr("app.mail_ingest.imaplib.IMAP4_SSL", FakeIMAPClient)


def make_email(subject: str, attachments: list[tuple[str, bytes, str, str]]) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "candidate@example.com"
    message["To"] = "hr@tapdata.io"
    message.set_content("您好，简历请查收。")
    for filename, content, maintype, subtype in attachments:
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()


def make_docx() -> bytes:
    document = Document()
    document.add_paragraph("五年数据平台经验，熟悉 Flink 与 Kafka。")
    document.add_paragraph("熟悉实时计算与数据仓库建设。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def docx_attachment(filename: str, content: bytes | None = None) -> tuple[str, bytes, str, str]:
    return (filename, content if content is not None else make_docx(), "application", DOCX_SUBTYPE)


def make_mail_app(tmp_path, *, configured: bool = True):
    """构造启用 IMAP 的测试应用（显式配置，避免读到 .env 的真实邮箱设置）。"""

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        imap_host="imap.example.com" if configured else None,
        imap_user="hr@tapdata.io" if configured else None,
        imap_password="secret" if configured else None,
        imap_poll_seconds=0,
    )
    return create_app(settings=settings)


def ingest_one_email_into_pool(client, monkeypatch, filename: str = "候选人.docx", subject: str = "简历"):
    """便捷入口：让一封带单个 DOCX 附件的邮件进入简历池，返回该池简历条目。"""

    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    install_fake_imap(monkeypatch, [make_email(subject, [docx_attachment(filename)])])
    result = client.post("/api/email/sync")
    assert result.status_code == 200
    assert result.json()["imported"] == 1
    return client.get("/api/pool/resumes").json()[0]
