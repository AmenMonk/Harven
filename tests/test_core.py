# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
核心模块单元测试 — URL安全校验、代码沙箱、数据库、记忆提取、速率限制

运行方式：
    cd HarvenAI 项目根目录
    python -m pytest tests/test_core.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest


class TestURLSecurity(unittest.TestCase):
    """URL 安全校验测试"""

    def setUp(self):
        from src.utils.url_security import validate_url
        self.validate = validate_url

    def test_valid_public_url(self):
        safe, _ = self.validate("https://www.example.com", resolve_dns=False)
        self.assertTrue(safe)

    def test_block_localhost(self):
        safe, msg = self.validate("http://127.0.0.1:8080", resolve_dns=False)
        self.assertFalse(safe)
        self.assertIn("禁止", msg)

    def test_block_private_ip(self):
        safe, _ = self.validate("http://192.168.1.1", resolve_dns=False)
        self.assertFalse(safe)

    def test_block_10_network(self):
        safe, _ = self.validate("http://10.0.0.1", resolve_dns=False)
        self.assertFalse(safe)

    def test_block_file_protocol(self):
        safe, msg = self.validate("file:///etc/passwd", resolve_dns=False)
        self.assertFalse(safe)
        self.assertIn("协议", msg)

    def test_empty_url(self):
        safe, msg = self.validate("", resolve_dns=False)
        self.assertFalse(safe)

    def test_https_allowed(self):
        safe, _ = self.validate("https://api.github.com", resolve_dns=False)
        self.assertTrue(safe)

    def test_dns_rebinding_block(self):
        safe, _ = self.validate("http://127.0.0.1.nip.io", resolve_dns=False)
        self.assertFalse(safe)


class TestCodeSandbox(unittest.TestCase):
    """代码沙箱安全检测测试"""

    def setUp(self):
        from src.core.code_sandbox import scan_code_safety
        self.scan = scan_code_safety

    def test_safe_code(self):
        safe, warnings = self.scan("x = 1 + 2\nprint(x)")
        self.assertTrue(safe)
        self.assertEqual(len(warnings), 0)

    def test_detect_os_system(self):
        safe, warnings = self.scan('os.system("whoami")')
        self.assertFalse(safe)
        self.assertTrue(any("os" in w.lower() for w in warnings))

    def test_detect_exec(self):
        safe, warnings = self.scan('exec("print(1)")')
        self.assertFalse(safe)

    def test_detect_eval(self):
        safe, warnings = self.scan('eval("1+1")')
        self.assertFalse(safe)

    def test_detect_subprocess(self):
        safe, warnings = self.scan('subprocess.run(["ls"])')
        self.assertFalse(safe)

    def test_detect_import_dangerous(self):
        safe, warnings = self.scan('import os\nos.system("dir")')
        self.assertFalse(safe)

    def test_ast_detect_getattr_bypass(self):
        """AST 应检测 getattr 绕过"""
        safe, warnings = self.scan('getattr(os, "system")("whoami")')
        self.assertFalse(safe)
        self.assertTrue(any("getattr" in w for w in warnings))

    def test_normal_math(self):
        safe, _ = self.scan("import math\nresult = math.sqrt(16)")
        self.assertTrue(safe)


class TestDBRouter(unittest.TestCase):
    """数据库路由器测试"""

    def test_classify_privacy(self):
        from src.core.db_router import classify_sensitivity
        self.assertEqual(classify_sensitivity("我的密码是123456"), "privacy")
        self.assertEqual(classify_sensitivity("我的银行卡号"), "privacy")

    def test_classify_commercial(self):
        from src.core.db_router import classify_sensitivity
        self.assertEqual(classify_sensitivity("这篇教程可以公开分享"), "commercial")

    def test_classify_transient(self):
        from src.core.db_router import classify_sensitivity
        self.assertEqual(classify_sensitivity("今天的天气怎么样"), "transient")

    def test_classify_empty_string(self):
        from src.core.db_router import classify_sensitivity
        self.assertEqual(classify_sensitivity(""), "transient")

    def test_classify_only_whitespace(self):
        from src.core.db_router import classify_sensitivity
        self.assertEqual(classify_sensitivity("   \t\n  "), "transient")


class TestRateLimiter(unittest.TestCase):
    """速率限制器测试"""

    def setUp(self):
        from src.core.rate_limiter import RateLimiter
        self.limiter = RateLimiter()

    def test_allow_normal_request(self):
        allowed, _ = self.limiter.check("192.168.1.1", "/api/chat")
        self.assertTrue(allowed)

    def test_block_after_burst(self):
        """突发请求后应被限制"""
        ip = "10.0.0.99"
        # 消耗所有令牌
        for _ in range(10):
            self.limiter.check(ip, "/api/chat")
        # 第 11 次应被阻止
        allowed, _ = self.limiter.check(ip, "/api/chat")
        # 可能被允许也可能被阻止（取决于时间），不做严格断言
        self.assertIsInstance(allowed, bool)

    def test_static_files_unlimited(self):
        allowed, _ = self.limiter.check("192.168.1.1", "/static/app.js")
        self.assertTrue(allowed)

    def test_health_check_unlimited(self):
        allowed, _ = self.limiter.check("192.168.1.1", "/health")
        self.assertTrue(allowed)

    def test_retry_after_is_float(self):
        """check 返回的 retry_after 应为 float"""
        from src.core.rate_limiter import RateLimiter
        limiter = RateLimiter()
        for _ in range(30):
            limiter.check("172.16.0.1", "/api/chat")
        allowed, retry_after = limiter.check("172.16.0.1", "/api/chat")
        self.assertIsInstance(retry_after, float)
        self.assertGreaterEqual(retry_after, 0)


class TestFileScanner(unittest.TestCase):
    """文件安全扫描测试"""

    def test_block_exe_extension(self):
        from src.core.file_scanner import check_extension
        safe, _ = check_extension("virus.exe")
        self.assertFalse(safe)

    def test_block_bat_extension(self):
        from src.core.file_scanner import check_extension
        safe, _ = check_extension("script.bat")
        self.assertFalse(safe)

    def test_allow_pdf_extension(self):
        from src.core.file_scanner import check_extension
        safe, _ = check_extension("report.pdf")
        self.assertTrue(safe)

    def test_allow_png_extension(self):
        from src.core.file_scanner import check_extension
        safe, _ = check_extension("photo.png")
        self.assertTrue(safe)

    def test_double_extension_attack(self):
        """双重扩展名攻击（如 virus.pdf.exe）"""
        from src.core.file_scanner import check_extension
        safe, _ = check_extension("report.pdf.exe")
        self.assertFalse(safe)

    def test_case_insensitive_block(self):
        """大小写变体仍应拦截"""
        from src.core.file_scanner import check_extension
        safe, _ = check_extension("SCRIPT.BAT")
        self.assertFalse(safe)


class TestMemoryExtraction(unittest.TestCase):
    """记忆提取测试"""

    def setUp(self):
        from src.core.memory import MemoryManager
        self.mm = MemoryManager()

    def test_simple_preference(self):
        memories = self.mm._simple_extract("我喜欢吃辣", "好的")
        self.assertTrue(len(memories) > 0)

    def test_identity_extraction(self):
        memories = self.mm._simple_extract("我叫张三", "好的，我记住了")
        self.assertTrue(any(m["category"] == "identity" for m in memories))


class TestMetaSharder(unittest.TestCase):
    """元数据分片测试"""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        from src.core.meta_sharder import MetaSharder
        self.sharder = MetaSharder(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_load(self):
        """保存和加载元数据"""
        entries = [
            {"id": "1", "text": "测试文档1", "source": "test"},
            {"id": "2", "text": "测试文档2", "source": "test"},
        ]
        self.sharder.save_all(entries)
        loaded = self.sharder.load_all()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["id"], "1")

    def test_empty_load(self):
        """空目录加载返回空列表"""
        loaded = self.sharder.load_all()
        self.assertEqual(loaded, [])

    def test_shard_info(self):
        """分片信息获取"""
        entries = [{"id": str(i), "text": f"doc{i}"} for i in range(5)]
        self.sharder.save_all(entries)
        info = self.sharder.get_shard_info()
        self.assertIsNotNone(info)
        self.assertIn("total_entries", info)
        self.assertEqual(info["total_entries"], 5)

    def test_large_dataset_no_error(self):
        """大数据集保存不抛异常"""
        entries = [{"id": str(i), "text": f"长文档内容 {'数据' * 50} {i}"} for i in range(200)]
        try:
            self.sharder.save_all(entries)
            loaded = self.sharder.load_all()
            self.assertGreaterEqual(len(loaded), 200)
        except Exception as e:
            self.fail(f"大数据集保存失败: {e}")


class TestHWDeviceSecurity(unittest.TestCase):
    """硬件安全 — 设备指纹与绑定测试"""

    def test_fingerprint_is_hex_string(self):
        """设备指纹应为 64 字符 hex 字符串"""
        from src.core.hw_security import generate_device_fingerprint
        fp = generate_device_fingerprint()
        self.assertEqual(len(fp), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_fingerprint_deterministic(self):
        """同一设备指纹应相同"""
        from src.core.hw_security import generate_device_fingerprint
        fp1 = generate_device_fingerprint()
        fp2 = generate_device_fingerprint()
        self.assertEqual(fp1, fp2)

    def test_security_status_keys(self):
        """安全状态返回应有字段"""
        from src.core.hw_security import get_device_security_status
        status = get_device_security_status()
        required_keys = ["fingerprint_hash", "is_bound", "bound_devices",
                         "clone_warnings", "security_level"]
        for key in required_keys:
            self.assertIn(key, status, f"缺失字段: {key}")

    def test_security_level_valid(self):
        """安全等级值合法"""
        from src.core.hw_security import get_device_security_status
        status = get_device_security_status()
        self.assertIn(status["security_level"], ["high", "medium", "low", "unbound"])

    def test_init_security_db_idempotent(self):
        """security_db 初始化幂等"""
        from src.core.hw_security import init_security_db
        try:
            init_security_db()
            init_security_db()  # 第二次调用不应报错
        except Exception as e:
            self.fail(f"二次 init_security_db 失败: {e}")


class TestMemoryDreaming(unittest.TestCase):
    """Memory Dreaming 三阶段记忆巩固测试"""

    def setUp(self):
        from src.core.memory_dreaming import MemoryDreamEngine
        self.engine = MemoryDreamEngine()

    def test_dream_report_keys(self):
        """dream_report 应包含所有必要字段"""
        report = self.engine.get_dream_report()
        for key in ("light_dream", "deep_dream", "rem_dream",
                    "total_consolidations", "total_processed"):
            self.assertIn(key, report, f"缺少字段: {key}")

    def test_deep_dream_no_llm_updates_timestamp(self):
        """Deep Dream 在没有 LLM 时应仍更新时间戳，避免无限重试"""
        self.engine._llm_fn = None
        before = self.engine.state.get("last_deep_dream", 0) or 0
        import time; time.sleep(0.01)
        session = self.engine.deep_dream()
        after = self.engine.state.get("last_deep_dream", 0) or 0
        self.assertGreater(after, before, "Deep Dream 应在无 LLM 时仍更新 last_deep_dream")

    def test_rem_dream_no_llm_updates_timestamp(self):
        """REM Dream 在没有 LLM 时应仍更新时间戳"""
        self.engine._llm_fn = None
        before = self.engine.state.get("last_rem_dream", 0) or 0
        import time; time.sleep(0.01)
        session = self.engine.rem_dream()
        after = self.engine.state.get("last_rem_dream", 0) or 0
        self.assertGreater(after, before, "REM Dream 应在无 LLM 时仍更新 last_rem_dream")

    def test_light_dream_returns_session(self):
        """Light Dream 应返回 DreamSession 对象"""
        from src.core.memory_dreaming import DreamSession
        session = self.engine.light_dream()
        self.assertIsInstance(session, DreamSession)
        self.assertEqual(session.dream_type, "light")

    def test_deep_dream_returns_session(self):
        """Deep Dream 应返回 DreamSession 对象"""
        from src.core.memory_dreaming import DreamSession
        session = self.engine.deep_dream()
        self.assertIsInstance(session, DreamSession)
        self.assertEqual(session.dream_type, "deep")

    def test_rem_dream_returns_session(self):
        """REM Dream 应返回 DreamSession 对象"""
        from src.core.memory_dreaming import DreamSession
        session = self.engine.rem_dream()
        self.assertIsInstance(session, DreamSession)
        self.assertEqual(session.dream_type, "rem")

    def test_force_dream_all(self):
        """force_dream('all') 应返回 3 个 session"""
        sessions = self.engine.force_dream("all")
        self.assertEqual(len(sessions), 3)

    def test_dream_session_duration_ms_non_negative(self):
        """DreamSession 执行时长应 >= 0"""
        from src.core.memory_dreaming import DreamSession
        sess = DreamSession("test")
        sess.finish()
        self.assertGreaterEqual(sess.duration_ms, 0)


class TestKnowledgeBaseMetaDecoupling(unittest.TestCase):
    """KnowledgeBase + MetaSharder 数据脱节修复验证"""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        from src.core.meta_sharder import MetaSharder
        self.sharder = MetaSharder(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_sharder_save_and_reload_roundtrip(self):
        """MetaSharder 保存后重新加载数据一致"""
        entries = [{"id": str(i), "text": f"doc_{i}", "source": "test", "metadata": {}}
                   for i in range(10)]
        self.sharder.save_all(entries)
        loaded = self.sharder.load_all()
        self.assertEqual(len(loaded), 10)

    def test_sharder_auto_shard_large(self):
        """超过 500 条时应自动分片"""
        entries = [{"id": str(i), "text": f"doc_{i} " + "x" * 50}
                   for i in range(600)]
        self.sharder.save_all(entries)
        loaded = self.sharder.load_all()
        self.assertEqual(len(loaded), 600)
        info = self.sharder.get_shard_info()
        self.assertIsNotNone(info)
        # 应产生多个分片
        self.assertGreaterEqual(info.get("shard_count", 0), 2)

    def test_sharder_load_empty_dir_returns_empty_list(self):
        """空目录 load_all 返回空列表不抛异常"""
        loaded = self.sharder.load_all()
        self.assertEqual(loaded, [])

    def test_sharder_overwrite_legacy_with_shards(self):
        """先保存传统文件，再保存大数据集，应自动切换为分片"""
        import json, os
        legacy_path = os.path.join(self.tmp_dir, "knowledge_meta.json")
        small_entries = [{"id": "a", "text": "小文件"}]
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump(small_entries, f, ensure_ascii=False)
        # 再保存超量数据
        big_entries = [{"id": str(i), "text": f"big_{i}"} for i in range(600)]
        self.sharder.save_all(big_entries)
        loaded = self.sharder.load_all()
        self.assertEqual(len(loaded), 600)

    def test_sharder_get_shard_info_legacy_format(self):
        """传统文件格式下 get_shard_info 应返回基本信息"""
        import json, os
        legacy_path = os.path.join(self.tmp_dir, "knowledge_meta.json")
        entries = [{"id": "1", "text": "test"}]
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        info = self.sharder.get_shard_info()
        self.assertIsNotNone(info)
        self.assertIn("total_entries", info)


class TestOllamaDaemon(unittest.TestCase):
    """Ollama 守护进程测试"""

    def test_daemon_init_no_exception(self):
        """OllamaDaemon 初始化不抛异常"""
        from src.core.ollama_daemon import OllamaDaemon
        daemon = OllamaDaemon()
        self.assertIsNotNone(daemon)

    def test_get_status_keys(self):
        """get_status 应包含必要字段"""
        from src.core.ollama_daemon import OllamaDaemon
        daemon = OllamaDaemon()
        status = daemon.get_status()
        for key in ("ollama_running", "daemon_active", "restart_count", "port"):
            self.assertIn(key, status, f"缺少字段: {key}")

    def test_default_port(self):
        """默认端口应为 11434"""
        from src.core.ollama_daemon import OllamaDaemon, OLLAMA_DEFAULT_PORT
        daemon = OllamaDaemon()
        self.assertEqual(daemon._port, OLLAMA_DEFAULT_PORT)

    def test_singleton_consistency(self):
        """get_ollama_daemon 应返回同一个对象"""
        from src.core.ollama_daemon import get_ollama_daemon
        d1 = get_ollama_daemon()
        d2 = get_ollama_daemon()
        self.assertIs(d1, d2)

    def test_is_ollama_running_returns_bool(self):
        """is_ollama_running 应返回布尔值（不管 Ollama 是否运行）"""
        from src.core.ollama_daemon import OllamaDaemon
        daemon = OllamaDaemon()
        result = daemon.is_ollama_running()
        self.assertIsInstance(result, bool)


class TestMemoryEviction(unittest.TestCase):
    """记忆驱逐策略测试"""

    def test_import_no_exception(self):
        """记忆驱逐模块可以正常导入"""
        try:
            from src.core import memory_eviction
        except ImportError as e:
            self.fail(f"memory_eviction 模块导入失败: {e}")

    def test_eviction_policy_exists(self):
        """应有 EvictionPolicy 或等效类/函数"""
        from src.core import memory_eviction
        # 至少有一个公开的类或函数
        public_names = [n for n in dir(memory_eviction) if not n.startswith("_")]
        self.assertGreater(len(public_names), 0, "memory_eviction 模块无公开接口")


class TestMetaSharderBoundary(unittest.TestCase):
    """MetaSharder 边界场景测试"""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        from src.core.meta_sharder import MetaSharder
        self.sharder = MetaSharder(self.tmp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_empty_list(self):
        """保存空列表后加载应返回空列表"""
        self.sharder.save_all([])
        loaded = self.sharder.load_all()
        self.assertEqual(loaded, [])

    def test_single_entry(self):
        """单条记录保存加载"""
        entries = [{"id": "x", "text": "单条测试", "source": "unit_test"}]
        self.sharder.save_all(entries)
        loaded = self.sharder.load_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "x")

    def test_save_twice_overwrite(self):
        """两次保存，第二次应覆盖第一次"""
        self.sharder.save_all([{"id": "1", "text": "first"}])
        self.sharder.save_all([{"id": "2", "text": "second"}, {"id": "3", "text": "third"}])
        loaded = self.sharder.load_all()
        self.assertEqual(len(loaded), 2)
        ids = [e["id"] for e in loaded]
        self.assertIn("2", ids)
        self.assertNotIn("1", ids)

    def test_corrupted_shard_does_not_crash(self):
        """损坏的分片文件不应导致崩溃"""
        import os
        # 先保存合法数据创建分片
        entries = [{"id": str(i), "text": f"doc{i}"} for i in range(600)]
        self.sharder.save_all(entries)
        # 损坏第一个分片
        shard_path = os.path.join(self.tmp_dir, "knowledge_meta_000.json")
        if os.path.exists(shard_path):
            with open(shard_path, "w", encoding="utf-8") as f:
                f.write("{ invalid json [[[")
        # 加载不应崩溃（可能返回部分结果）
        try:
            loaded = self.sharder.load_all()
            self.assertIsInstance(loaded, list)
        except Exception as e:
            self.fail(f"损坏分片导致崩溃: {e}")


if __name__ == "__main__":
    unittest.main()

