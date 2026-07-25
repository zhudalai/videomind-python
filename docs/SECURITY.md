# VideoMind 安全设计

> JWT 鉴权 + API Key AES-GCM 加密 + 速率限流 + 审计日志 + CORS/CSRF 防护
> 核心参考：Ragent `infra-auth/` + DOVideo-AI 安全模块 + OWASP ASVS L2 标准

---

## 1. 安全架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          安全边界 (Security Perimeter)                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        API Gateway / Ingress                        │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │   │
│  │  │  TLS     │  │  CORS    │  │  Rate    │  │  WAF Rules       │  │   │
│  │  │  Term    │  │  Policy  │  │  Limit   │  │  (SQLi/XSS/Path) │  │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                       │
│  ┌────────────────────────────────┴────────────────────────────────────┐   │
│  │                      Authentication Layer                           │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │   │
│  │  │ JWT Access  │  │ JWT Refresh │  │ API Key     │  │  Session  │  │   │
│  │  │  Token      │  │  Token      │  │  (AES-GCM)  │  │  Cookie   │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘  │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                       │
│  ┌────────────────────────────────┴────────────────────────────────────┐   │
│  │                      Authorization Layer (RBAC + ABAC)              │   │
│  │  Roles: admin, user, analyst, viewer                                 │   │
│  │  Permissions: video:read, video:write, analysis:create, admin:*     │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                       │
│  ┌────────────────────────────────┴────────────────────────────────────┐   │
│  │                      Audit & Monitoring                             │   │
│  │  Structured JSON Logs → Loki/ELK → Alerting                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 认证体系

### 2.1 JWT 双 Token 机制

```python
class JWTConfig:
    # 访问令牌：短期，仅在内存中
    ACCESS_TOKEN_EXPIRE_MINUTES = 15
    # 刷新令牌：长期，HttpOnly Cookie 存储
    REFRESH_TOKEN_EXPIRE_DAYS = 30
    # 签名算法
    ALGORITHM = "RS256"  # 非对称，私钥签名，公钥验证
    # 密钥轮换
    KEY_ROTATION_DAYS = 90


class TokenPayload(BaseModel):
    sub: str                    # user_id
    email: str
    roles: list[str]            # ["user", "analyst"]
    permissions: list[str]      # ["video:read", "analysis:create"]
    type: Literal["access", "refresh"]
    jti: str                    # JWT ID，用于撤销
    iat: int
    exp: int
    device_id: str | None = None  # 设备指纹


class JWTService:
    def __init__(self, private_key: str, public_key: str, config: JWTConfig):
        self.private_key = private_key
        self.public_key = public_key
        self.config = config
        self.redis: Redis  # 用于 token 撤销列表
    
    def create_access_token(self, user: User, device_id: str | None = None) -> str:
        payload = TokenPayload(
            sub=str(user.id),
            email=user.email,
            roles=[r.name for r in user.roles],
            permissions=self._get_permissions(user.roles),
            type="access",
            jti=uuid4().hex,
            iat=int(time.time()),
            exp=int(time.time()) + self.config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            device_id=device_id
        )
        return jwt.encode(payload.model_dump(), self.private_key, algorithm=self.config.ALGORITHM)
    
    def create_refresh_token(self, user: User, device_id: str | None = None) -> str:
        payload = TokenPayload(
            sub=str(user.id),
            email=user.email,
            roles=[r.name for r in user.roles],
            permissions=[],
            type="refresh",
            jti=uuid4().hex,
            iat=int(time.time()),
            exp=int(time.time()) + self.config.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            device_id=device_id
        )
        return jwt.encode(payload.model_dump(), self.private_key, algorithm=self.config.ALGORITHM)
    
    def verify_token(self, token: str, token_type: Literal["access", "refresh"]) -> TokenPayload:
        try:
            payload = jwt.decode(
                token,
                self.public_key,
                algorithms=[self.config.ALGORITHM],
                audience="videomind",
                issuer="videomind-auth"
            )
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError()
        except jwt.InvalidTokenError:
            raise InvalidTokenError()
        
        # 检查类型
        if payload.get("type") != token_type:
            raise InvalidTokenError("Token type mismatch")
        
        # 检查撤销列表
        if self._is_revoked(payload["jti"]):
            raise TokenRevokedError()
        
        return TokenPayload(**payload)
    
    def revoke_token(self, jti: str, ttl: int):
        """加入撤销列表（Redis SET，TTL 同 token 过期时间）"""
        self.redis.setex(f"revoked:{jti}", ttl, "1")
    
    def _is_revoked(self, jti: str) -> bool:
        return self.redis.exists(f"revoked:{jti}")
    
    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        """刷新令牌轮换：验证 refresh → 签发新 access + 新 refresh → 撤销旧 refresh"""
        payload = self.verify_token(refresh_token, "refresh")
        
        # 撤销旧 refresh
        self.revoke_token(payload.jti, self.config.REFRESH_TOKEN_EXPIRE_DAYS * 86400)
        
        # 获取用户最新权限
        user = await self.user_repo.get(UUID(payload.sub))
        if not user or not user.is_active:
            raise UserInactiveError()
        
        device_id = payload.device_id
        new_access = self.create_access_token(user, device_id)
        new_refresh = self.create_refresh_token(user, device_id)
        
        return new_access, new_refresh
```

### 2.2 API Key 管理（AES-GCM 加密存储）

```python
class APIKeyManager:
    """
    API Key 设计：
    - 前缀标识：vk_live_ / vk_test_ / vk_dev_
    - 格式：{prefix}_{random32}.{checksum8}
    - 存储：仅存储哈希（argon2id）+ 加密明文（AES-GCM，用于最后4位显示）
    - 权限：绑定角色权限集，支持 IP 白名单、调用频率限制
    """
    
    PREFIX_MAP = {
        "live": "vk_live_",
        "test": "vk_test_",
        "dev": "vk_dev_"
    }
    
    def __init__(self, master_key: bytes, redis: Redis):
        # master_key: 32 bytes，从 KMS 或环境变量加载
        self.cipher = AESGCM(master_key)
        self.redis = redis
    
    def generate_key(self, user_id: UUID, name: str, env: str = "live", 
                     permissions: list[str] | None = None, 
                     ip_whitelist: list[str] | None = None,
                     rate_limit: int = 1000) -> APIKey:
        # 生成随机部分
        random_part = secrets.token_urlsafe(24)  # 32 bytes -> 32 chars
        prefix = self.PREFIX_MAP[env]
        raw_key = f"{prefix}{random_part}"
        
        # 校验和（前 8 位 SHA256）
        checksum = hashlib.sha256(raw_key.encode()).hexdigest()[:8]
        full_key = f"{raw_key}.{checksum}"
        
        # 存储哈希（验证用）
        key_hash = hash_password(raw_key)  # argon2id
        
        # 加密存储明文（用于管理界面显示后 4 位）
        nonce = secrets.token_bytes(12)
        encrypted = self.cipher.encrypt(nonce, raw_key.encode(), None)
        encrypted_b64 = base64.b64encode(nonce + encrypted).decode()
        
        api_key = APIKey(
            id=uuid4(),
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            encrypted_key=encrypted_b64,
            key_prefix=raw_key[:12],  # vk_live_abc123
            key_suffix=raw_key[-4:],   # 后 4 位明文显示
            permissions=permissions or [],
            ip_whitelist=ip_whitelist or [],
            rate_limit=rate_limit,
            env=env,
            created_at=datetime.utcnow(),
            last_used_at=None,
            expires_at=None,
            is_active=True
        )
        
        await self.repo.insert(api_key)
        return api_key
    
    async def verify_key(self, provided_key: str, client_ip: str) -> APIKey | None:
        # 1. 格式校验
        if not self._validate_format(provided_key):
            return None
        
        raw_key, checksum = provided_key.rsplit(".", 1)
        if hashlib.sha256(raw_key.encode()).hexdigest()[:8] != checksum:
            return None
        
        # 2. 前缀查找
        prefix = raw_key[:12]
        candidates = await self.repo.find_by_prefix(prefix)
        
        for candidate in candidates:
            if not candidate.is_active:
                continue
            if candidate.expires_at and candidate.expires_at < datetime.utcnow():
                continue
            
            # IP 白名单
            if candidate.ip_whitelist and client_ip not in candidate.ip_whitelist:
                continue
            
            # 验证哈希
            if verify_password(raw_key, candidate.key_hash):
                # 更新最后使用时间
                await self.repo.update_last_used(candidate.id)
                return candidate
        
        return None
    
    def _validate_format(self, key: str) -> bool:
        # vk_{env}_{32chars}.{8chars}
        pattern = r"^vk_(live|test|dev)_[A-Za-z0-9_-]{32}\.[a-f0-9]{8}$"
        return bool(re.match(pattern, key))
    
    def decrypt_for_display(self, encrypted_b64: str) -> str:
        """解密用于管理界面显示完整 key（需二次确认）"""
        data = base64.b64decode(encrypted_b64)
        nonce, ciphertext = data[:12], data[12:]
        return self.cipher.decrypt(nonce, ciphertext, None).decode()
```

---

## 3. 授权体系 (RBAC + ABAC)

```python
class Permission(StrEnum):
    # 视频
    VIDEO_READ = "video:read"
    VIDEO_WRITE = "video:write"
    VIDEO_DELETE = "video:delete"
    VIDEO_INGEST = "video:ingest"
    
    # 分析
    ANALYSIS_CREATE = "analysis:create"
    ANALYSIS_READ = "analysis:read"
    ANALYSIS_DELETE = "analysis:delete"
    
    # RAG
    RAG_QUERY = "rag:query"
    RAG_MANAGE_KB = "rag:manage_kb"
    
    # 系统
    ADMIN_USERS = "admin:users"
    ADMIN_SYSTEM = "admin:system"
    ADMIN_AUDIT = "admin:audit"


ROLE_PERMISSIONS: dict[str, list[Permission]] = {
    "viewer": [Permission.VIDEO_READ, Permission.ANALYSIS_READ, Permission.RAG_QUERY],
    "user": [Permission.VIDEO_READ, Permission.VIDEO_WRITE, Permission.VIDEO_INGEST,
             Permission.ANALYSIS_CREATE, Permission.ANALYSIS_READ, Permission.RAG_QUERY],
    "analyst": [Permission.VIDEO_READ, Permission.VIDEO_WRITE, Permission.VIDEO_INGEST,
                Permission.ANALYSIS_CREATE, Permission.ANALYSIS_READ, Permission.ANALYSIS_DELETE,
                Permission.RAG_QUERY, Permission.RAG_MANAGE_KB],
    "admin": [p for p in Permission]  # 所有权限
}


class AuthorizationService:
    def __init__(self, user_repo: UserRepo, casbin_enforcer: Enforcer):
        self.user_repo = user_repo
        self.enforcer = casbin_enforcer  # Casbin 用于 ABAC 复杂策略
    
    async def check_permission(self, user_id: UUID, permission: Permission, 
                               resource: Resource | None = None) -> bool:
        user = await self.user_repo.get(user_id)
        if not user or not user.is_active:
            return False
        
        # 1. RBAC 基础检查
        user_perms = set()
        for role in user.roles:
            user_perms.update(ROLE_PERMISSIONS.get(role.name, []))
        
        if permission not in user_perms:
            return False
        
        # 2. ABAC 资源级检查（如：只能操作自己上传的视频）
        if resource:
            return await self._check_abac(user, permission, resource)
        
        return True
    
    async def _check_abac(self, user: User, permission: Permission, resource: Resource) -> bool:
        # 资源所有者检查
        if resource.owner_id == user.id:
            return True
        
        # 组织/团队共享检查
        if resource.team_id and user.team_id == resource.team_id:
            # 团队内权限：viewer 只读，user 可写
            if permission in (Permission.VIDEO_READ, Permission.ANALYSIS_READ):
                return True
            if permission in (Permission.VIDEO_WRITE, Permission.ANALYSIS_CREATE) and any(r.name in ("user", "analyst") for r in user.roles):
                return True
        
        # 公开资源
        if resource.visibility == "public" and permission in (Permission.VIDEO_READ, Permission.RAG_QUERY):
            return True
        
        return False
```

---

## 4. 速率限流

```python
class RateLimiter:
    """
    多层限流：
    1. 全局：IP 级（防 DDoS）
    2. 用户级：user_id（防滥用）
    3. API Key 级：key_id（配额控制）
    4. 端点级：敏感接口额外限制
    
    算法：滑动窗口 + 令牌桶（Redis Lua 保证原子性）
    """
    
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def check_limit(self, 
                          key: str, 
                          limit: int, 
                          window_seconds: int,
                          burst: int | None = None) -> RateLimitResult:
        """
        滑动窗口计数器：
        - 记录每个请求的时间戳（Sorted Set）
        - 清理窗口外的旧记录
        - 统计当前窗口内计数
        """
        now = time.time()
        window_start = now - window_seconds
        
        lua = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        local burst = tonumber(ARGV[4])
        
        -- 清理过期
        redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
        
        -- 当前计数
        local count = redis.call('ZCARD', key)
        
        -- 令牌桶：检查突发
        local tokens_key = key .. ':tokens'
        local tokens = tonumber(redis.call('GET', tokens_key) or burst)
        if tokens > burst then tokens = burst end
        
        local allowed = 0
        local retry_after = 0
        
        if count < limit and tokens > 0 then
            -- 允许
            redis.call('ZADD', key, now, now .. ':' .. math.random())
            redis.call('EXPIRE', key, math.ceil(ARGV[5]))
            redis.call('DECR', tokens_key)
            allowed = 1
        else
            -- 拒绝
            local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
            if #oldest > 0 then
                retry_after = math.ceil(tonumber(oldest[2]) + ARGV[5] - now)
            end
        end
        
        return {allowed, count + 1, limit, retry_after}
        """
        
        result = await self.redis.eval(lua, 1, key, now, window_start, limit, burst or limit, window_seconds)
        allowed, current, limit, retry_after = result
        
        return RateLimitResult(
            allowed=bool(allowed),
            current=current,
            limit=limit,
            remaining=max(0, limit - current),
            retry_after=retry_after if not allowed else 0,
            reset_at=int(now + window_seconds)
        )


# FastAPI 依赖注入
async def rate_limit_dependency(
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter)
):
    # IP 级全局限流
    ip = request.client.host
    global_result = await limiter.check_limit(f"global:ip:{ip}", 1000, 60)  # 1000/min
    
    # 用户级限流
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        user_result = await limiter.check_limit(f"user:{user_id}", 200, 60)  # 200/min
        if not user_result.allowed:
            raise RateLimitExceededError(user_result)
    
    # API Key 级限流
    api_key_id = getattr(request.state, "api_key_id", None)
    if api_key_id:
        key_result = await limiter.check_limit(f"apikey:{api_key_id}", 1000, 60)
        if not key_result.allowed:
            raise RateLimitExceededError(key_result)
    
    # 响应头
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": str(global_result.limit),
        "X-RateLimit-Remaining": str(global_result.remaining),
        "X-RateLimit-Reset": str(global_result.reset_at)
    }
```

---

## 5. 审计日志

```python
class AuditLogger:
    """结构化审计日志：写入 Loki/ELK，支持实时告警"""
    
    def __init__(self, log_client: LogClient):
        self.log_client = log_client
    
    async def log(self, event: AuditEvent):
        # 统一字段
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": "INFO",
            "service": "videomind-api",
            "trace_id": event.trace_id or get_current_trace_id(),
            "span_id": get_current_span_id(),
            
            # 事件标识
            "event_type": event.event_type,      # AUTH_LOGIN, VIDEO_UPLOAD, ANALYSIS_START, etc.
            "event_category": event.category,    # AUTH, DATA, ADMIN, SECURITY
            
            # 主体
            "user_id": str(event.user_id) if event.user_id else None,
            "api_key_id": str(event.api_key_id) if event.api_key_id else None,
            "ip": event.ip,
            "user_agent": event.user_agent,
            "device_id": event.device_id,
            
            # 客体
            "resource_type": event.resource_type,  # video, analysis, user, api_key
            "resource_id": str(event.resource_id) if event.resource_id else None,
            
            # 动作
            "action": event.action,                # create, read, update, delete, execute
            "result": event.result,                # success, failure, denied
            "error_code": event.error_code,
            "error_message": event.error_message,
            
            # 上下文
            "metadata": event.metadata,            # 灵活扩展字段
            
            # 合规
            "compliance_tags": event.compliance_tags  # ["GDPR", "PIPL", "SOC2"]
        }
        
        # 敏感字段脱敏
        log_entry = self._sanitize(log_entry)
        
        await self.log_client.send(log_entry)
    
    def _sanitize(self, entry: dict) -> dict:
        sensitive_keys = {"password", "token", "secret", "key", "authorization", "cookie"}
        def sanitize_obj(obj):
            if isinstance(obj, dict):
                return {k: "***" if k.lower() in sensitive_keys else sanitize_obj(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_obj(v) for v in obj]
            return obj
        return sanitize_obj(entry)


# 审计事件类型枚举
class AuditEventType(StrEnum):
    # 认证
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"
    AUTH_PASSWORD_CHANGE = "auth.password_change"
    AUTH_MFA_ENABLE = "auth.mfa_enable"
    
    # API Key
    APIKEY_CREATE = "apikey.create"
    APIKEY_REVOKE = "apikey.revoke"
    APIKEY_USE = "apikey.use"
    
    # 视频
    VIDEO_UPLOAD = "video.upload"
    VIDEO_DOWNLOAD = "video.download"
    VIDEO_DELETE = "video.delete"
    VIDEO_INGEST_START = "video.ingest_start"
    VIDEO_INGEST_COMPLETE = "video.ingest_complete"
    
    # 分析
    ANALYSIS_CREATE = "analysis.create"
    ANALYSIS_EXECUTE = "analysis.execute"
    ANALYSIS_COMPLETE = "analysis.complete"
    
    # RAG
    RAG_QUERY = "rag.query"
    RAG_KB_CREATE = "rag.kb_create"
    
    # 管理
    ADMIN_USER_CREATE = "admin.user_create"
    ADMIN_USER_DELETE = "admin.user_delete"
    ADMIN_ROLE_ASSIGN = "admin.role_assign"
    ADMIN_CONFIG_CHANGE = "admin.config_change"
    
    # 安全
    SECURITY_RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    SECURITY_SUSPICIOUS_ACTIVITY = "security.suspicious_activity"
    SECURITY_PERMISSION_DENIED = "security.permission_denied"
```

---

## 6. CORS / CSRF / 安全头

```python
# FastAPI 中间件配置
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # 仅生产域名，无 "*"
    allow_credentials=True,                # 需要 Cookie 认证时
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS  # ["api.videomind.com", "localhost"]
)


# 安全响应头中间件
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    
    # HSTS
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    
    # CSP
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: https:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    
    # 其他安全头
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    
    # 移除服务器信息
    response.headers.pop("Server", None)
    
    return response


# CSRF 保护（针对 Cookie 认证的状态变更接口）
class CSRFMiddleware:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key
    
    async def __call__(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # 仅对 Cookie 认证的请求检查 CSRF
            if request.cookies.get("refresh_token"):
                csrf_token = request.headers.get("X-CSRF-Token")
                if not csrf_token or not self._validate_csrf(csrf_token, request):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF token missing or invalid"}
                    )
        
        response = await call_next(request)
        
        # 颁发新 CSRF Token（双重提交模式）
        if request.method == "GET" and not request.cookies.get("csrf_token"):
            new_token = self._generate_csrf_token(request)
            response.set_cookie(
                "csrf_token", new_token,
                httponly=True, secure=True, samesite="strict", max_age=3600
            )
            response.headers["X-CSRF-Token"] = new_token
        
        return response
```

---

## 7. 数据加密

```python
class DataEncryption:
    """字段级加密：PII、API Key 明文、Webhook Secret 等"""
    
    def __init__(self, master_key: bytes):
        # 从 KMS 获取或环境变量派生
        self.field_cipher = AESGCM(master_key)
    
    def encrypt_field(self, plaintext: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = self.field_cipher.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()
    
    def decrypt_field(self, encrypted_b64: str) -> str:
        data = base64.b64decode(encrypted_b64)
        nonce, ciphertext = data[:12], data[12:]
        return self.field_cipher.decrypt(nonce, ciphertext, None).decode()


# SQLAlchemy 类型装饰器
class EncryptedString(TypeDecorator):
    impl = String
    cache_ok = True
    
    def __init__(self, *args, encryption: DataEncryption, **kwargs):
        super().__init__(*args, **kwargs)
        self.encryption = encryption
    
    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return self.encryption.encrypt_field(value)
    
    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return self.encryption.decrypt_field(value)


# 使用示例
class User(Base):
    __tablename__ = "user"
    
    id = Column(UUID, primary_key=True)
    email = Column(String(255), unique=True, index=True)
    # 手机号加密存储
    phone = Column(EncryptedString(255, encryption=encryption), nullable=True)
    # 真实姓名加密存储
    real_name = Column(EncryptedString(100, encryption=encryption), nullable=True)
```

---

## 8. 安全配置清单

```yaml
# config/security.yaml
jwt:
  algorithm: "RS256"
  access_token_expire_minutes: 15
  refresh_token_expire_days: 30
  key_rotation_days: 90
  issuer: "videomind-auth"
  audience: "videomind"

api_key:
  prefix_map:
    live: "vk_live_"
    test: "vk_test_"
    dev: "vk_dev_"
  hash_algorithm: "argon2id"
  encryption: "AES-256-GCM"

rate_limit:
  global:
    ip_per_minute: 1000
  user:
    per_minute: 200
  api_key:
    per_minute: 1000
  endpoints:
    "/api/v1/auth/login": 5/minute
    "/api/v1/auth/register": 3/minute
    "/api/v1/videos/ingest": 10/minute

cors:
  allow_origins:
    - "https://videomind.com"
    - "https://app.videomind.com"
  allow_credentials: true

security_headers:
  hsts_max_age: 31536000
  csp: "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; ..."
  frame_options: "DENY"
  content_type_options: "nosniff"

audit:
  enabled: true
  log_level: "INFO"
  compliance_tags: ["PIPL", "GDPR"]
  retention_days: 365

encryption:
  field_encryption_enabled: true
  kms_provider: "local"  # 或 aws_kms, hashicorp_vault
  key_rotation_enabled: true
```

---

## 9. 事件检测与响应

```python
class SecurityMonitor:
    """实时安全事件检测"""
    
    def __init__(self, redis: Redis, alert_webhook: str):
        self.redis = redis
        self.alert_webhook = alert_webhook
    
    async def check_anomalies(self, event: AuditEvent):
        checks = [
            self._check_brute_force(event),
            self._check_credential_stuffing(event),
            self._check_impossible_travel(event),
            self._check_api_abuse(event),
            self._check_data_exfiltration(event)
        ]
        
        for check in checks:
            if await check:
                await self._trigger_alert(event, check.__name__)
    
    async def _check_brute_force(self, event: AuditEvent) -> bool:
        if event.event_type != AuditEventType.AUTH_LOGIN_FAILED:
            return False
        
        key = f"sec:bruteforce:ip:{event.ip}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 900)  # 15 分钟窗口
        
        return count >= 10  # 15 分钟内 10 次失败
    
    async def _check_impossible_travel(self, event: AuditEvent) -> bool:
        if not event.user_id or event.event_type != AuditEventType.AUTH_LOGIN:
            return False
        
        last_login = await self.redis.get(f"sec:last_login:{event.user_id}")
        if not last_login:
            return False
        
        last_data = json.loads(last_login)
        # 计算物理距离/时间是否合理（简化版）
        # 实际应用可集成 MaxMind GeoIP
        return False
    
    async def _trigger_alert(self, event: AuditEvent, rule_name: str):
        alert = {
            "alert_type": "security_anomaly",
            "rule": rule_name,
            "severity": "high",
            "event": event.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
        await self.http_client.post(self.alert_webhook, json=alert)
        logger.warning(f"Security alert triggered: {rule_name}", extra=alert)
```

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [MODEL-GATEWAY.md](MODEL-GATEWAY.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [OBSERVABILITY.md](OBSERVABILITY.md)