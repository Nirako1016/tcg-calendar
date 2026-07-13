"""
企业微信消息加解密模块
实现企业微信回调消息的 AES-CBC-256 解密/加密 和 签名验证
"""
import base64
import hashlib
import os
import struct
import time
import xml.etree.ElementTree as ET
from typing import Optional

from Crypto.Cipher import AES

# AES 块大小
BLOCK_SIZE = 32


def _pkcs7_pad(data: bytes) -> bytes:
    """PKCS7 填充"""
    pad_len = BLOCK_SIZE - (len(data) % BLOCK_SIZE)
    if pad_len == 0:
        pad_len = BLOCK_SIZE
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes) -> bytes:
    """PKCS7 去填充"""
    pad_len = data[-1]
    if pad_len < 1 or pad_len > BLOCK_SIZE:
        return data
    return data[:-pad_len]


def _get_aes_key(encoding_aes_key: str) -> bytes:
    """从 EncodingAESKey 派生 AES 密钥"""
    return base64.b64decode(encoding_aes_key + "=")


def verify_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """
    验证消息签名
    返回计算出的 sha1 签名
    """
    items = sorted([token, timestamp, nonce, encrypt])
    raw = "".join(items).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def decrypt_message(encoding_aes_key: str, encrypt: str, corp_id: str) -> str:
    """
    解密企业微信消息
    返回解密后的 XML 字符串
    """
    aes_key = _get_aes_key(encoding_aes_key)
    iv = aes_key[:16]

    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(base64.b64decode(encrypt))
    decrypted = _pkcs7_unpad(decrypted)

    # 格式: random(16) + msg_len(4, big endian) + msg + corp_id
    msg_len = struct.unpack("!I", decrypted[16:20])[0]
    msg = decrypted[20:20 + msg_len].decode("utf-8")
    received_corp_id = decrypted[20 + msg_len:].decode("utf-8")

    if received_corp_id != corp_id:
        raise ValueError(f"CorpID 不匹配: 期望 {corp_id}, 收到 {received_corp_id}")

    return msg


def encrypt_message(encoding_aes_key: str, reply: str, corp_id: str) -> str:
    """
    加密回复消息
    返回 base64 编码的加密字符串
    """
    aes_key = _get_aes_key(encoding_aes_key)
    iv = aes_key[:16]

    # 随机16字节 + msg_len(4) + msg + corp_id
    random_bytes = os.urandom(16)
    msg_bytes = reply.encode("utf-8")
    corp_bytes = corp_id.encode("utf-8")
    msg_len = struct.pack("!I", len(msg_bytes))

    plain = random_bytes + msg_len + msg_bytes + corp_bytes
    plain = _pkcs7_pad(plain)

    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(plain)
    return base64.b64encode(encrypted).decode("utf-8")


def build_encrypted_reply(
    token: str,
    encoding_aes_key: str,
    corp_id: str,
    reply_msg: str,
) -> str:
    """
    构建完整的加密回复 XML
    """
    encrypt = encrypt_message(encoding_aes_key, reply_msg, corp_id)
    timestamp = str(int(time.time()))
    nonce = str(int(time.time() * 1000) % 100000)

    signature = verify_signature(token, timestamp, nonce, encrypt)

    xml = (
        f"<xml>"
        f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
        f"<TimeStamp>{timestamp}</TimeStamp>"
        f"<Nonce><![CDATA[{nonce}]]></Nonce>"
        f"</xml>"
    )
    return xml


def parse_callback_xml(xml_str: str) -> dict:
    """
    解析企业微信回调 XML，提取 Encrypt 字段
    """
    root = ET.fromstring(xml_str)
    result = {}
    for child in root:
        result[child.tag] = child.text
    return result


def extract_message(xml_str: str) -> Optional[str]:
    """从回调 XML 中提取 Encrypt 字段"""
    data = parse_callback_xml(xml_str)
    return data.get("Encrypt")


def parse_decrypted_xml(xml_str: str) -> dict:
    """
    解析解密后的 XML，提取消息内容
    """
    root = ET.fromstring(xml_str)
    result = {}
    for child in root:
        result[child.tag] = child.text
    return result


def build_text_reply(from_user: str, to_user: str, content: str) -> str:
    """
    构建文本回复消息 XML（未加密）
    """
    return (
        f"<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        f"<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        f"</xml>"
    )
