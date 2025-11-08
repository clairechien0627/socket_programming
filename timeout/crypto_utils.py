"""
加密工具模組 - 混合加密實作
使用 RSA + AES + HMAC 提供端到端加密
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
from cryptography.hazmat.primitives import hashes, serialization, hmac
from cryptography.hazmat.backends import default_backend
import os
import base64
import json
import time


class CryptoManager:
    """
    混合加密管理器
    
    功能:
    - RSA-2048: 用於金鑰交換
    - AES-256-CBC: 用於訊息加密
    - HMAC-SHA256: 用於訊息完整性驗證
    - 時間戳記 + Nonce: 防止重放攻擊
    """
    
    def __init__(self):
        # RSA 金鑰對 (2048 bits)
        self.private_key = None
        self.public_key = None
        
        # AES 會話金鑰 (256 bits)
        self.aes_key = None
        self.aes_iv = None  # 初始化向量 (128 bits)
        
        # 已接收訊息的 nonce (防重放攻擊)
        self.received_nonces = set()
        self.max_nonces = 10000  # 最多記住 10000 個 nonce
    
    def generate_rsa_keypair(self) -> None:
        """
        生成 RSA 金鑰對
        - 公鑰: 用於加密
        - 私鑰: 用於解密
        """
        print("🔑 正在生成 RSA-2048 金鑰對...")
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,  # 標準指數
            key_size=2048,          # 2048 bits (推薦)
            backend=default_backend()
        )
        self.public_key = self.private_key.public_key()
        print("✅ RSA 金鑰對生成完成")
    
    def generate_aes_key(self) -> None:
        """
        生成 AES 會話金鑰
        - 金鑰: 256 bits (32 bytes)
        - IV: 128 bits (16 bytes)
        """
        self.aes_key = os.urandom(32)  # 256 bits
        self.aes_iv = os.urandom(16)   # 128 bits
        print("🔐 AES-256 會話金鑰已生成")
    
    def export_public_key(self) -> str:
        """
        匯出 RSA 公鑰 (PEM 格式)
        
        Returns:
            PEM 格式的公鑰字串
        """
        if not self.public_key:
            raise ValueError("尚未生成 RSA 金鑰對")
        
        pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return pem.decode('utf-8')
    
    @staticmethod
    def import_public_key(pem_str: str):
        """
        匯入 RSA 公鑰
        
        Args:
            pem_str: PEM 格式的公鑰字串
            
        Returns:
            RSA 公鑰物件
        """
        return serialization.load_pem_public_key(
            pem_str.encode('utf-8'),
            backend=default_backend()
        )
    
    def encrypt_with_rsa(self, data: bytes, public_key) -> bytes:
        """
        使用 RSA 公鑰加密
        
        用途: 加密 AES 會話金鑰
        算法: RSA-OAEP (最佳非對稱加密填充)
        
        Args:
            data: 要加密的資料 (最大 190 bytes for 2048-bit key)
            public_key: RSA 公鑰
            
        Returns:
            加密後的資料
        """
        return public_key.encrypt(
            data,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
    
    def decrypt_with_rsa(self, encrypted_data: bytes) -> bytes:
        """
        使用 RSA 私鑰解密
        
        Args:
            encrypted_data: 加密的資料
            
        Returns:
            解密後的資料
        """
        if not self.private_key:
            raise ValueError("尚未生成 RSA 私鑰")
        
        return self.private_key.decrypt(
            encrypted_data,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
    
    def encrypt_message(self, plaintext: str) -> dict:
        """
        使用 AES 加密訊息 (含完整性保護)
        
        流程:
        1. 加入時間戳記和 nonce (防重放攻擊)
        2. AES-256-CBC 加密
        3. HMAC-SHA256 計算訊息認證碼
        
        Args:
            plaintext: 明文訊息
            
        Returns:
            包含密文和 MAC 的字典
        """
        if not self.aes_key or not self.aes_iv:
            raise ValueError("尚未設定 AES 金鑰")
        
        # 1. 構建訊息 (加入元數據)
        message = {
            'content': plaintext,
            'timestamp': time.time(),
            'nonce': base64.b64encode(os.urandom(16)).decode('utf-8')
        }
        data = json.dumps(message).encode('utf-8')
        
        # 2. PKCS7 填充 (使資料長度為 16 的倍數)
        padding_length = 16 - (len(data) % 16)
        padded_data = data + bytes([padding_length] * padding_length)
        
        # 3. AES-256-CBC 加密
        cipher = Cipher(
            algorithms.AES(self.aes_key),
            modes.CBC(self.aes_iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()
        
        # 4. 計算 HMAC (訊息認證碼)
        h = hmac.HMAC(self.aes_key, hashes.SHA256(), backend=default_backend())
        h.update(ciphertext)
        mac = h.finalize()
        
        return {
            'ciphertext': base64.b64encode(ciphertext).decode('utf-8'),
            'mac': base64.b64encode(mac).decode('utf-8')
        }
    
    def decrypt_message(self, encrypted_message: dict, check_replay: bool = True) -> str:
        """
        使用 AES 解密訊息 (含完整性驗證)
        
        流程:
        1. 驗證 HMAC (確保未被竄改)
        2. AES-256-CBC 解密
        3. 檢查時間戳記 (防重放攻擊)
        4. 檢查 nonce (防重放攻擊)
        
        Args:
            encrypted_message: 包含密文和 MAC 的字典
            check_replay: 是否檢查重放攻擊
            
        Returns:
            解密後的明文訊息
            
        Raises:
            ValueError: 訊息被竄改或重放攻擊
        """
        if not self.aes_key or not self.aes_iv:
            raise ValueError("尚未設定 AES 金鑰")
        
        # 1. 驗證 HMAC (完整性檢查)
        ciphertext = base64.b64decode(encrypted_message['ciphertext'])
        mac = base64.b64decode(encrypted_message['mac'])
        
        h = hmac.HMAC(self.aes_key, hashes.SHA256(), backend=default_backend())
        h.update(ciphertext)
        try:
            h.verify(mac)
        except Exception:
            raise ValueError("❌ 訊息完整性驗證失敗! (可能被竄改)")
        
        # 2. AES-256-CBC 解密
        cipher = Cipher(
            algorithms.AES(self.aes_key),
            modes.CBC(self.aes_iv),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(ciphertext) + decryptor.finalize()
        
        # 3. 移除 PKCS7 填充
        padding_length = padded_data[-1]
        data = padded_data[:-padding_length]
        
        # 4. 解析 JSON
        try:
            message = json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("❌ 訊息解密失敗! (無效的訊息格式)")
        
        if check_replay:
            # 5. 檢查時間戳記 (防重放攻擊: 5分鐘內有效)
            current_time = time.time()
            message_time = message.get('timestamp', 0)
            if current_time - message_time > 300:  # 5 分鐘
                raise ValueError(f"❌ 訊息過期! (發送於 {int(current_time - message_time)} 秒前)")
            
            # 6. 檢查 nonce (防止重放攻擊)
            nonce = message.get('nonce', '')
            if nonce in self.received_nonces:
                raise ValueError("❌ 檢測到重放攻擊! (nonce 重複)")
            
            # 記錄 nonce
            self.received_nonces.add(nonce)
            
            # 限制記憶體使用 (只保留最近 10000 個)
            if len(self.received_nonces) > self.max_nonces:
                # 移除最舊的 1000 個 (FIFO)
                oldest = list(self.received_nonces)[:1000]
                self.received_nonces -= set(oldest)
        
        return message['content']
    
    def get_aes_key_bundle(self) -> bytes:
        """
        取得 AES 金鑰包 (金鑰 + IV)
        
        用途: 用 RSA 加密後傳送給對方
        
        Returns:
            金鑰 + IV (48 bytes)
        """
        if not self.aes_key or not self.aes_iv:
            raise ValueError("尚未生成 AES 金鑰")
        
        return self.aes_key + self.aes_iv
    
    def set_aes_key_bundle(self, key_bundle: bytes) -> None:
        """
        設定 AES 金鑰包
        
        Args:
            key_bundle: 金鑰 + IV (48 bytes)
        """
        if len(key_bundle) != 48:
            raise ValueError("無效的金鑰包長度")
        
        self.aes_key = key_bundle[:32]  # 前 32 bytes
        self.aes_iv = key_bundle[32:]   # 後 16 bytes
        print("✅ AES 會話金鑰已設定")


def demonstrate_encryption():
    """
    演示加密流程
    """
    print("\n" + "="*60)
    print("🔐 加密演示")
    print("="*60)
    
    # 1. 伺服器生成 RSA 金鑰對
    print("\n[伺服器端]")
    server_crypto = CryptoManager()
    server_crypto.generate_rsa_keypair()
    
    # 2. 客戶端生成 AES 會話金鑰
    print("\n[客戶端]")
    client_crypto = CryptoManager()
    client_crypto.generate_aes_key()
    
    # 3. 客戶端用伺服器公鑰加密 AES 金鑰
    print("\n[金鑰交換]")
    server_public_key = CryptoManager.import_public_key(
        server_crypto.export_public_key()
    )
    
    encrypted_aes_key = client_crypto.encrypt_with_rsa(
        client_crypto.get_aes_key_bundle(),
        server_public_key
    )
    print(f"✅ AES 金鑰已用 RSA 加密 (長度: {len(encrypted_aes_key)} bytes)")
    
    # 4. 伺服器解密獲得 AES 金鑰
    aes_key_bundle = server_crypto.decrypt_with_rsa(encrypted_aes_key)
    server_crypto.set_aes_key_bundle(aes_key_bundle)
    
    # 5. 測試訊息加密
    print("\n[訊息加密]")
    original_message = "Hello, this is a secret message! 🔒"
    print(f"明文: {original_message}")
    
    encrypted = client_crypto.encrypt_message(original_message)
    print(f"密文: {encrypted['ciphertext'][:50]}...")
    print(f"MAC: {encrypted['mac'][:20]}...")
    
    # 6. 測試訊息解密
    print("\n[訊息解密]")
    decrypted = server_crypto.decrypt_message(encrypted)
    print(f"解密: {decrypted}")
    
    # 7. 驗證
    assert decrypted == original_message, "解密失敗!"
    print("\n✅ 加密/解密驗證成功!")
    
    # 8. 測試篡改檢測
    print("\n[篡改檢測測試]")
    tampered = encrypted.copy()
    tampered['ciphertext'] = base64.b64encode(b"tampered data").decode()
    try:
        server_crypto.decrypt_message(tampered)
        print("❌ 未檢測到篡改!")
    except ValueError as e:
        print(f"✅ 成功檢測到篡改: {e}")
    
    # 9. 測試重放攻擊
    print("\n[重放攻擊測試]")
    try:
        # 第一次解密成功
        server_crypto.decrypt_message(encrypted)
        # 第二次應該失敗 (nonce 重複)
        server_crypto.decrypt_message(encrypted)
        print("❌ 未檢測到重放攻擊!")
    except ValueError as e:
        print(f"✅ 成功檢測到重放攻擊: {e}")
    
    print("\n" + "="*60)


if __name__ == "__main__":
    demonstrate_encryption()
