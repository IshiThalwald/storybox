from fastapi import APIRouter, HTTPException
from datetime import datetime
from app.utils import log_manager
import app.config.settings as settings
import app.vertex.config as app_config
from app.utils.auth import verify_web_password
from app.utils.logging import log, vertex_log_manager
from app.config.persistence import save_settings
from app.utils.stats import api_stats_manager
import json

# 导入 Vertex 凭证管理相关组件
from app.vertex.credentials_manager import (
    CredentialManager,
    parse_multiple_json_credentials,
)

# 引入重新初始化 vertex 的函数
from app.vertex.vertex_ai_init import (
    init_vertex_ai as re_init_vertex_ai_function,
    reset_global_fallback_client,
)

# 创建路由器
dashboard_router = APIRouter(prefix="/api", tags=["dashboard"])

# 全局变量引用，将在 init_dashboard_router 中设置
response_cache_manager = None
active_requests_manager = None
credential_manager = None  # Vertex 凭证管理器


def init_dashboard_router(
    cache_mgr,
    active_req_mgr,
    cred_mgr=None,
    key_mgr=None,  # 保留该参数以兼容原有调用，但不再使用
):
    """初始化仪表盘路由器"""
    global response_cache_manager, active_requests_manager, credential_manager
    response_cache_manager = cache_mgr
    active_requests_manager = active_req_mgr
    credential_manager = cred_mgr
    # key_mgr 不再使用，仅保留参数以兼容外部调用
    return dashboard_router


async def run_blocking_init_vertex():
    """使用当前的 credential_manager 重新执行 init_vertex_ai"""
    try:
        if credential_manager is None:
            log("warning", "Credential Manager 不存在，将创建一个新的实例用于初始化")
            temp_credential_manager = CredentialManager()
            credentials_count = temp_credential_manager.get_total_credentials()
            log("info", f"临时 Credential Manager 已创建，包含 {credentials_count} 个凭证")

            success = await re_init_vertex_ai_function(
                credential_manager=temp_credential_manager
            )
        else:
            credentials_count = credential_manager.get_total_credentials()
            log("info", f"使用现有 Credential Manager 进行初始化，当前有 {credentials_count} 个凭证")
            success = await re_init_vertex_ai_function(
                credential_manager=credential_manager
            )

        if success:
            log("info", "异步重新执行 init_vertex_ai 成功，以响应 Google Credentials JSON 的更新。")
        else:
            log("warning", "异步重新执行 init_vertex_ai 失败或未完成。")
    except Exception as e:
        log("error", f"执行 run_blocking_init_vertex 时出错: {e}")


@dashboard_router.get("/dashboard-data")
async def get_dashboard_data():
    """获取仪表盘数据的 API 端点，用于动态刷新"""
    # 清理过期数据，确保统计最新
    await api_stats_manager.maybe_cleanup()
    await response_cache_manager.clean_expired()
    active_requests_manager.clean_completed()

    now = datetime.now()

    # 获取调用次数统计
    last_24h_calls = api_stats_manager.get_calls_last_24h()
    hourly_calls = api_stats_manager.get_calls_last_hour(now)
    minute_calls = api_stats_manager.get_calls_last_minute(now)

    # 获取 Token 消耗统计
    last_24h_tokens = api_stats_manager.get_tokens_last_24h()
    hourly_tokens = api_stats_manager.get_tokens_last_hour(now)
    minute_tokens = api_stats_manager.get_tokens_last_minute(now)

    # 获取时间序列数据
    time_series_data, tokens_time_series = api_stats_manager.get_time_series_data(30, now)

    # 根据 ENABLE_VERTEX 决定返回哪种日志
    if settings.ENABLE_VERTEX:
        recent_logs = vertex_log_manager.get_recent_logs(500)
    else:
        recent_logs = log_manager.get_recent_logs(500)

    # 缓存统计
    total_cache = response_cache_manager.cur_cache_num

    # 活跃请求统计
    active_count = len(active_requests_manager.active_requests)
    active_done = sum(1 for task in active_requests_manager.active_requests.values() if task.done())
    active_pending = active_count - active_done

    # 获取 Vertex 凭证数量
    credentials_count = 0
    if credential_manager is not None:
        credentials_count = credential_manager.get_total_credentials()

    # 返回 JSON 数据（已移除普通 API 密钥相关字段）
    return {
        # 普通密钥相关字段设为固定值，保持前端兼容
        "key_count": 0,
        "model_count": 0,
        "available_models": [],
        "api_key_stats": {},
        # Vertex 凭证数量
        "credentials_count": credentials_count,
        # 其他通用字段
        "retry_count": settings.MAX_RETRY_NUM,
        "last_24h_calls": last_24h_calls,
        "hourly_calls": hourly_calls,
        "minute_calls": minute_calls,
        "last_24h_tokens": last_24h_tokens,
        "hourly_tokens": hourly_tokens,
        "minute_tokens": minute_tokens,
        "calls_time_series": time_series_data,
        "tokens_time_series": tokens_time_series,
        "current_time": datetime.now().strftime("%H:%M:%S"),
        "logs": recent_logs,
        "max_requests_per_minute": settings.MAX_REQUESTS_PER_MINUTE,
        "max_requests_per_day_per_ip": settings.MAX_REQUESTS_PER_DAY_PER_IP,
        "local_version": settings.version["local_version"],
        "remote_version": settings.version["remote_version"],
        "has_update": settings.version["has_update"],
        "fake_streaming": settings.FAKE_STREAMING,
        "fake_streaming_interval": settings.FAKE_STREAMING_INTERVAL,
        "random_string": settings.RANDOM_STRING,
        "random_string_length": settings.RANDOM_STRING_LENGTH,
        "search_mode": settings.search["search_mode"],
        "search_prompt": settings.search["search_prompt"],
        "cache_entries": total_cache,
        "cache_expiry_time": settings.CACHE_EXPIRY_TIME,
        "max_cache_entries": settings.MAX_CACHE_ENTRIES,
        "active_count": active_count,
        "active_done": active_done,
        "active_pending": active_pending,
        "concurrent_requests": settings.CONCURRENT_REQUESTS,
        "increase_concurrent_on_failure": settings.INCREASE_CONCURRENT_ON_FAILURE,
        "max_concurrent_requests": settings.MAX_CONCURRENT_REQUESTS,
        "enable_vertex": settings.ENABLE_VERTEX,
        "enable_vertex_express": settings.ENABLE_VERTEX_EXPRESS,
        "vertex_express_api_key": bool(settings.VERTEX_EXPRESS_API_KEY),
        "google_credentials_json": bool(settings.GOOGLE_CREDENTIALS_JSON),
        "max_retry_num": settings.MAX_RETRY_NUM,
        "max_empty_responses": settings.MAX_EMPTY_RESPONSES,
    }


@dashboard_router.post("/reset-stats")
async def reset_stats(password_data: dict):
    """重置 API 调用统计数据"""
    try:
        if not isinstance(password_data, dict):
            raise HTTPException(status_code=422, detail="请求体格式错误：应为 JSON 对象")

        password = password_data.get("password")
        if not password:
            raise HTTPException(status_code=400, detail="缺少密码参数")
        if not isinstance(password, str):
            raise HTTPException(status_code=422, detail="密码参数类型错误：应为字符串")
        if not verify_web_password(password):
            raise HTTPException(status_code=401, detail="密码错误")

        await api_stats_manager.reset()
        return {"status": "success", "message": "API 调用统计数据已重置"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置失败：{str(e)}")


@dashboard_router.post("/update-config")
async def update_config(config_data: dict):
    """更新配置项（已移除普通 API 密钥相关分支）"""
    try:
        if not isinstance(config_data, dict):
            raise HTTPException(status_code=422, detail="请求体格式错误：应为 JSON 对象")

        password = config_data.get("password")
        if not password:
            raise HTTPException(status_code=400, detail="缺少密码参数")
        if not isinstance(password, str):
            raise HTTPException(status_code=422, detail="密码参数类型错误：应为字符串")
        if not verify_web_password(password):
            raise HTTPException(status_code=401, detail="密码错误")

        config_key = config_data.get("key")
        config_value = config_data.get("value")

        if not config_key:
            raise HTTPException(status_code=400, detail="缺少配置项键名")

        # 根据配置项类型进行验证和更新
        if config_key == "max_requests_per_minute":
            try:
                value = int(config_value)
                if value <= 0:
                    raise ValueError("每分钟请求限制必须大于0")
                settings.MAX_REQUESTS_PER_MINUTE = value
                log("info", f"每分钟请求限制已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "max_requests_per_day_per_ip":
            try:
                value = int(config_value)
                if value <= 0:
                    raise ValueError("每IP每日请求限制必须大于0")
                settings.MAX_REQUESTS_PER_DAY_PER_IP = value
                log("info", f"每IP每日请求限制已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "fake_streaming":
            if not isinstance(config_value, bool):
                raise HTTPException(status_code=422, detail="参数类型错误：应为布尔值")
            settings.FAKE_STREAMING = config_value
            log("info", f"假流式请求已更新为：{config_value}")

            # 同步更新 vertex 配置
            try:
                import app.vertex.config as vertex_config
                vertex_config.FAKE_STREAMING_ENABLED = config_value
                vertex_config.update_config("FAKE_STREAMING", config_value)
                log("info", f"已同步更新 Vertex 中的假流式设置为：{config_value}")
            except Exception as e:
                log("warning", f"更新 Vertex 假流式设置时出错: {str(e)}")

        elif config_key == "enable_vertex_express":
            if not isinstance(config_value, bool):
                raise HTTPException(status_code=422, detail="参数类型错误：应为布尔值")
            settings.ENABLE_VERTEX_EXPRESS = config_value
            log("info", f"Vertex Express 已更新为：{config_value}")

        elif config_key == "vertex_express_api_key":
            if not isinstance(config_value, str):
                raise HTTPException(status_code=422, detail="参数类型错误：应为字符串")

            if not config_value or config_value.lower() == "true":
                log("info", "Vertex Express API Key 未更新，因为值为空或为 'true'")
            else:
                settings.VERTEX_EXPRESS_API_KEY = config_value
                app_config.VERTEX_EXPRESS_API_KEY_VAL = [
                    key.strip() for key in config_value.split(",") if key.strip()
                ]
                log("info", f"Vertex Express API Key 已更新，共 {len(app_config.VERTEX_EXPRESS_API_KEY_VAL)} 个有效密钥")

                # 尝试刷新模型配置
                try:
                    from app.vertex.model_loader import refresh_models_config_cache
                    refresh_success = await refresh_models_config_cache()
                    if refresh_success:
                        log("info", "更新 Express API Key 后成功刷新模型配置")
                    else:
                        log("warning", "更新 Express API Key 后刷新模型配置失败，将使用默认模型或现有缓存")
                except Exception as e:
                    log("warning", f"尝试刷新模型配置时出错: {str(e)}")

        elif config_key == "fake_streaming_interval":
            try:
                value = float(config_value)
                if value <= 0:
                    raise ValueError("假流式间隔必须大于0")
                settings.FAKE_STREAMING_INTERVAL = value
                log("info", f"假流式间隔已更新为：{value}")

                try:
                    import app.vertex.config as vertex_config
                    vertex_config.update_config("FAKE_STREAMING_INTERVAL", value)
                    log("info", f"已同步更新 Vertex 中的假流式间隔设置为：{value}")
                except Exception as e:
                    log("warning", f"更新 Vertex 假流式间隔设置时出错: {str(e)}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "random_string":
            if not isinstance(config_value, bool):
                raise HTTPException(status_code=422, detail="参数类型错误：应为布尔值")
            settings.RANDOM_STRING = config_value
            log("info", f"随机字符串已更新为：{config_value}")

        elif config_key == "random_string_length":
            try:
                value = int(config_value)
                if value <= 0:
                    raise ValueError("随机字符串长度必须大于0")
                settings.RANDOM_STRING_LENGTH = value
                log("info", f"随机字符串长度已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "search_mode":
            if not isinstance(config_value, bool):
                raise HTTPException(status_code=422, detail="参数类型错误：应为布尔值")
            settings.search["search_mode"] = config_value
            log("info", f"联网搜索模式已更新为：{config_value}")
            # 已移除普通 API 密钥的模型刷新逻辑，如需刷新 Vertex 模型可在此添加

        elif config_key == "search_prompt":
            if not isinstance(config_value, str):
                raise HTTPException(status_code=422, detail="参数类型错误：应为字符串")
            settings.search["search_prompt"] = config_value
            log("info", f"联网搜索提示已更新为：{config_value}")

        elif config_key == "concurrent_requests":
            try:
                value = int(config_value)
                if value <= 0:
                    raise ValueError("并发请求数必须大于0")
                settings.CONCURRENT_REQUESTS = value
                log("info", f"并发请求数已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "increase_concurrent_on_failure":
            try:
                value = int(config_value)
                if value < 0:
                    raise ValueError("失败时增加的并发数不能为负数")
                settings.INCREASE_CONCURRENT_ON_FAILURE = value
                log("info", f"失败时增加的并发数已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "max_concurrent_requests":
            try:
                value = int(config_value)
                if value <= 0:
                    raise ValueError("最大并发请求数必须大于0")
                settings.MAX_CONCURRENT_REQUESTS = value
                log("info", f"最大并发请求数已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "enable_vertex":
            if not isinstance(config_value, bool):
                raise HTTPException(status_code=422, detail="参数类型错误：应为布尔值")
            settings.ENABLE_VERTEX = config_value
            log("info", f"Vertex AI 已更新为：{config_value}")

        elif config_key == "google_credentials_json":
            if not isinstance(config_value, str):
                raise HTTPException(status_code=422, detail="参数类型错误：Google Credentials JSON 应为字符串")

            if not config_value or config_value.lower() == "true":
                log("info", "Google Credentials JSON 未更新，因为值为空或为 'true'")
                save_settings()
                return {"status": "success", "message": f"配置项 {config_key} 未更新，值为空或为 'true'"}

            # 验证 JSON 格式
            if config_value:
                try:
                    temp_parsed = parse_multiple_json_credentials(config_value)
                    if not temp_parsed:
                        try:
                            json.loads(config_value)
                        except json.JSONDecodeError:
                            raise HTTPException(
                                status_code=422,
                                detail="Google Credentials JSON 格式无效。它既不是有效的单个 JSON 对象，也不是逗号分隔的多个 JSON 对象。",
                            )
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=422, detail=f"Google Credentials JSON 预检查失败: {str(e)}")

            settings.GOOGLE_CREDENTIALS_JSON = config_value
            log("info", "Google Credentials JSON 设置已更新（内容未记录）。")

            reset_global_fallback_client()

            if credential_manager is not None:
                cleared_count = credential_manager.clear_json_string_credentials()
                log("info", f"从 CredentialManager 中清除了 {cleared_count} 个先前由 JSON 字符串加载的凭据。")

                if config_value:
                    parsed_json_objects = parse_multiple_json_credentials(config_value)
                    if parsed_json_objects:
                        loaded_count = credential_manager.load_credentials_from_json_list(parsed_json_objects)
                        if loaded_count > 0:
                            log("info", f"从更新的 Google Credentials JSON 中加载了 {loaded_count} 个凭据到 CredentialManager。")
                        else:
                            log("warning", "尝试加载 Google Credentials JSON 凭据失败，没有凭据被成功加载。")
                    else:
                        try:
                            single_cred = json.loads(config_value)
                            if credential_manager.add_credential_from_json(single_cred):
                                log("info", "作为单个 JSON 对象成功加载了一个凭据。")
                            else:
                                log("warning", "作为单个 JSON 对象加载凭据失败。")
                        except json.JSONDecodeError:
                            log("warning", "Google Credentials JSON 无法作为 JSON 对象解析。")
                        except Exception as e:
                            log("warning", f"尝试加载单个 JSON 凭据时出错: {str(e)}")
                else:
                    log("info", "Google Credentials JSON 已被清空。CredentialManager 中来自 JSON 字符串的凭据已被移除。")

                if credential_manager.get_total_credentials() == 0:
                    log("warning", "警告：当前没有可用的凭证。Vertex AI 功能可能无法正常工作。")
            else:
                log("warning", "CredentialManager 未初始化，无法加载 Google Credentials JSON。")

            save_settings()

            try:
                if credential_manager is None:
                    log("warning", "重新初始化 Vertex AI 时发现 credential_manager 为 None")
                else:
                    log("info", f"开始重新初始化 Vertex AI，当前凭证数: {credential_manager.get_total_credentials()}")
                await run_blocking_init_vertex()
                log("info", "Vertex AI 服务重新初始化完成")

                from app.vertex.model_loader import refresh_models_config_cache
                refresh_success = await refresh_models_config_cache()
                if refresh_success:
                    log("info", "成功刷新模型配置缓存")
                else:
                    log("warning", "刷新模型配置缓存失败，将使用默认模型或现有缓存")
            except Exception as e:
                log("error", f"重新初始化 Vertex AI 服务时出错: {str(e)}")

        elif config_key == "max_retry_num":
            try:
                value = int(config_value)
                if value <= 0:
                    raise ValueError("最大重试次数必须大于0")
                settings.MAX_RETRY_NUM = value
                log("info", f"最大重试次数已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        elif config_key == "max_empty_responses":
            try:
                value = int(config_value)
                if value < 0:
                    raise ValueError("空响应重试次数不能为负数")
                settings.MAX_EMPTY_RESPONSES = value
                log("info", f"空响应重试次数已更新为：{value}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"参数类型错误：{str(e)}")

        else:
            raise HTTPException(status_code=400, detail=f"不支持的配置项：{config_key}")

        save_settings()
        return {"status": "success", "message": f"配置项 {config_key} 已更新"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败：{str(e)}")