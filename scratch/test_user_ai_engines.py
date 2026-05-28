import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from matchpredict.db import prediction_db
from matchpredict.integrations.ai_predictor import AIFootballPredictor

def test_engines():
    print("--- 启动用户自定义 AI 引擎单元与安全测试 ---")
    
    # 1. 确保数据库表和字段存在
    if prediction_db:
        try:
            prediction_db.ensure_ai_config_columns()
            print("[✓] 数据库 Schema 迁移检查通过")
        except Exception as e:
            print(f"[✗] 数据库 Schema 迁移失败: {e}")
            return
    # 模拟一个测试用户
    test_user_id = 99999
    try:
        with prediction_db.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (test_user_id,))
                cur.execute("""
                    INSERT INTO users (id, username, password_hash, email, user_type)
                    VALUES (%s, 'test_ai_user', 'pass', 'test@ai.com', 'user')
                """, (test_user_id,))
                conn.commit()
        print("[✓] 数据库创建测试用户成功")
    except Exception as e:
        print(f"[✗] 数据库创建测试用户失败: {e}")
        return
    
    # 2. 模拟 Kiro CLI (通过 echo 包含环境变量以测试参数注入)
    print("\n测试 1: 联调本地 CLI 引擎并校验共享参数加载与环境变量注入")
    config_kiro = {
        'ai_engine_type': 'kiro_cli',
        'ai_api_url': 'https://custom-gateway.io/v1',
        'ai_api_key': 'my-secret-test-key-999',
        'ai_model': 'my-custom-cli-model',
        # 在 macOS / Linux 环境下，我们可以利用 sh -c 来输出注入的环境变量以进行校验
        'cli_path_kiro': 'sh -c "echo Model:$AI_MODEL,Key:$GEMINI_API_KEY"',
        'cli_path_antigravity': 'antigravity',
        'cli_path_cursor': 'cursor'
    }
    
    try:
        prediction_db.save_user_ai_config(test_user_id, config_kiro)
        print("[✓] 保存用户 Kiro CLI 配置成功")
        
        # 实例化预测器
        predictor = AIFootballPredictor(user_id=test_user_id)
        print(f"[✓] 预测器加载引擎类型: {predictor.engine_type}")
        assert predictor.engine_type == 'kiro_cli'
        
        # 校验模型、Key、URL 参数是否被加载进实例
        print(f"[✓] 实例加载的模型: {predictor.model_name}")
        print(f"[✓] 实例加载的 Base URL: {predictor.base_url}")
        assert predictor.model_name == 'my-custom-cli-model'
        assert predictor.api_key == 'my-secret-test-key-999'
        assert predictor.base_url == 'https://custom-gateway.io/v1'
        
        # 执行预测并校验环境参数是否注入进命令行进程
        test_prompt = "测试 Kiro"
        res = predictor._call_ai_model(test_prompt)
        print(f"[✓] CLI 执行响应: {res.strip()}")
        assert "Model:my-custom-cli-model" in res
        assert "Key:my-secret-test-key-999" in res
        print("[✓] 共享参数成功读取并完美注入本地 CLI 的执行子进程！")
        
    except Exception as e:
        print(f"[✗] Kiro CLI 驱动与注入测试失败: {e}")
        
    # 3. 模拟异常 Fallback (不存在的 CLI 路径)
    print("\n测试 2: 本地 CLI 缺失情况下的优雅异常捕捉")
    config_missing = {
        'ai_engine_type': 'cursor_cli',
        'ai_api_url': '',
        'ai_api_key': '',
        'ai_model': '',
        'cli_path_kiro': 'kiro',
        'cli_path_antigravity': 'antigravity',
        'cli_path_cursor': 'nonexistent-cli-command-xyz'
    }
    
    try:
        prediction_db.save_user_ai_config(test_user_id, config_missing)
        predictor = AIFootballPredictor(user_id=test_user_id)
        assert predictor.engine_type == 'cursor_cli'
        
        res = predictor._call_ai_model("测试不存在命令")
        print(f"[✗] 不应该成功执行 nonexistent 命令, 返回了: {res}")
    except Exception as e:
        error_msg = str(e)
        print(f"[✓] 捕获到了预期的错误: {error_msg}")
        assert "未找到" in error_msg or "不存在" in error_msg or "FileNotFoundError" in error_msg or "No such file" in error_msg
        
    # 4. 模拟系统默认 Fallback
    print("\n测试 3: 系统默认 (System Default) Fallback")
    config_system = {
        'ai_engine_type': 'system',
        'ai_api_url': '',
        'ai_api_key': '',
        'ai_model': '',
        'cli_path_kiro': 'kiro',
        'cli_path_antigravity': 'antigravity',
        'cli_path_cursor': 'cursor'
    }
    
    try:
        prediction_db.save_user_ai_config(test_user_id, config_system)
        predictor = AIFootballPredictor(user_id=test_user_id)
        print(f"[✓] 预测器加载引擎类型: {predictor.engine_type}")
        assert predictor.engine_type == 'system'
        
    except Exception as e:
        print(f"[✗] Fallback 测试失败: {e}")

    # 5. 测试后台 CLI 登录接口 (/api/user/login-cli)
    print("\n测试 4: 测试后台 CLI 登录接口 (/api/user/login-cli)")
    try:
        from app import app
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = test_user_id
                sess['username'] = 'test_ai_user'
            
            # 使用 sh -c "echo logged-in" 模拟登录指令并检验环境变量注入
            resp = client.post('/api/user/login-cli', json={
                'ai_engine_type': 'kiro_cli',
                'cli_path': 'sh -c "echo Mocked Kiro Login Success: $GEMINI_API_KEY"',
                'ai_api_key': 'test-secret-api-key',
                'ai_api_url': 'https://my-api-url.com',
                'ai_model': 'my-model-name'
            })
            data = resp.get_json()
            print(f"[✓] 登录接口响应: {data}")
            assert resp.status_code == 200
            assert data['success'] is True
            assert "Mocked Kiro Login Success: test-secret-api-key" in data['output']
            print("[✓] 后端 CLI 登录接口 (/api/user/login-cli) 调用成功并且标准输入/环境变量注入正确！")
            
            # 测试未找到可执行命令的异常处理
            resp_missing = client.post('/api/user/login-cli', json={
                'ai_engine_type': 'cursor_cli',
                'cli_path': 'nonexistent-command-path-12345',
                'ai_api_key': 'test-secret-api-key'
            })
            print(f"[✓] 缺失命令接口响应: {resp_missing.get_json()}")
            assert resp_missing.status_code == 404
            assert resp_missing.get_json()['success'] is False
            print("[✓] 缺失命令异常捕捉与友好提示校验成功！")
            
    except Exception as e:
        print(f"[✗] 登录接口测试失败: {e}")

    # 清理测试数据
    try:
        with prediction_db.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (test_user_id,))
                conn.commit()
        print("\n[✓] 清理测试数据成功")
    except Exception:
        pass

    print("\n--- 所有核心引擎测试验证完毕 ---")

if __name__ == '__main__':
    test_engines()
