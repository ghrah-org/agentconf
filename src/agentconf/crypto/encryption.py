# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AES-256-GCM 加密 + 环境变量主密钥管理"""

import base64
import os
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agentconf.exceptions import EncryptionError, MasterKeyNotFoundError

logger = logging.getLogger(__name__)

ENV_KEY_NAME = "agentconf_MASTER_KEY"
MASTER_KEY_SIZE = 32  # 256 bits


class CryptoManager:
    """加密管理器

    从环境变量 agentconf_MASTER_KEY 读取 AES-256-GCM 主密钥。
    主密钥为 base64 编码的 32 字节随机密钥。
    """

    def __init__(self) -> None:
        self._master_key: bytes | None = None
        self._initialize_master_key()

    def _initialize_master_key(self) -> None:
        """从环境变量加载主密钥"""
        env_key = os.environ.get(ENV_KEY_NAME)
        if env_key is None:
            raise MasterKeyNotFoundError(
                f"未设置环境变量 {ENV_KEY_NAME}。"
                f"请运行以下命令生成并设置主密钥:\n"
                f"  export {ENV_KEY_NAME}=$(python -c 'from agentconf.crypto import CryptoManager; print(CryptoManager.generate_env_key())')\n"
                f"或将以下内容添加到 ~/.bashrc / ~/.zshrc:\n"
                f"  export {ENV_KEY_NAME}=\"<your-base64-key>\""
            )

        try:
            self._master_key = base64.b64decode(env_key)
        except Exception as e:
            raise MasterKeyNotFoundError(
                f"环境变量 {ENV_KEY_NAME} 中的密钥不是有效的 base64 编码: {e}"
            )

        if len(self._master_key) != MASTER_KEY_SIZE:
            raise MasterKeyNotFoundError(
                f"环境变量 {ENV_KEY_NAME} 中的密钥长度不正确: "
                f"期望 {MASTER_KEY_SIZE} 字节，实际 {len(self._master_key)} 字节"
            )

        logger.debug("从环境变量成功加载主密钥")

    def encrypt(self, plaintext: str) -> str:
        """使用 AES-256-GCM 加密字符串

        Args:
            plaintext: 待加密的明文字符串

        Returns:
            base64 编码的 (IV + ciphertext + tag)

        Raises:
            EncryptionError: 加密失败
        """
        if self._master_key is None:
            raise EncryptionError("主密钥未初始化")

        try:
            aesgcm = AESGCM(self._master_key)
            iv = os.urandom(12)  # 96-bit IV for GCM
            ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
            # ciphertext 已包含 tag（最后16字节）
            return base64.b64encode(iv + ciphertext).decode("ascii")
        except Exception as e:
            raise EncryptionError(f"加密失败: {e}") from e

    def decrypt(self, encrypted: str) -> str:
        """使用 AES-256-GCM 解密字符串

        Args:
            encrypted: base64 编码的 (IV + ciphertext + tag)

        Returns:
            解密后的明文字符串

        Raises:
            EncryptionError: 解密失败
        """
        if self._master_key is None:
            raise EncryptionError("主密钥未初始化")

        try:
            data = base64.b64decode(encrypted)
            iv = data[:12]
            ciphertext = data[12:]
            aesgcm = AESGCM(self._master_key)
            plaintext = aesgcm.decrypt(iv, ciphertext, None)
            return plaintext.decode("utf-8")
        except EncryptionError:
            raise
        except Exception as e:
            raise EncryptionError(f"解密失败: {e}") from e

    @staticmethod
    def generate_env_key() -> str:
        """生成一个可用于环境变量的 base64 编码主密钥"""
        return base64.b64encode(os.urandom(MASTER_KEY_SIZE)).decode("ascii")
