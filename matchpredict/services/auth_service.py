# -*- coding: utf-8 -*-
"""Authentication and user account business logic."""
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from datetime import date

from matchpredict.data import prediction_db
from matchpredict.utils.auth import hash_password

try:
    from scripts.ai_predictor import AIFootballPredictor
except ImportError:
    AIFootballPredictor = None

_cli_login_jobs: dict = {}


class AuthService:
    def __init__(self, db=None):
        self._db = db or prediction_db

    def validate_register(self, username: str, email: str, password: str) -> dict | None:
        if not username or len(username) < 3:
            return {'message': '用户名长度至少3个字符', '_http_status': 400}
        if not email or '@' not in email:
            return {'message': '请输入有效的邮箱地址', '_http_status': 400}
        if not password or len(password) < 6:
            return {'message': '密码长度至少6个字符', '_http_status': 400}
        return None

    def register(self, username: str, email: str, password: str) -> dict:
        err = self.validate_register(username, email, password)
        if err:
            return {'success': False, **err}
        if not self._db:
            return {'success': False, 'message': '注册失败：数据库服务不可用', '_http_status': 500}
        password_hash = hash_password(password)
        if self._db.create_user(username, email, password_hash):
            return {'success': True, 'message': '注册成功，请登录', '_http_status': 200}
        return {
            'success': False,
            'message': '注册失败：用户名或邮箱已存在，或数据库写入失败',
            '_http_status': 409,
        }

    def login(self, username: str, password: str) -> dict:
        if not username or not password:
            return {'success': False, 'message': '请输入用户名和密码', '_http_status': 400}
        if not self._db:
            return {'success': False, 'message': '登录失败：数据库服务不可用', '_http_status': 500}
        user = self._db.authenticate_user(username, hash_password(password))
        if not user:
            return {'success': False, 'message': '用户名或密码错误', '_http_status': 401}
        already_checked = user.get('last_checkin_date') == date.today()
        return {
            'success': True,
            'message': '登录成功',
            '_http_status': 200,
            '_session_user': user,
            'user': {
                'username': user['username'],
                'user_type': user['user_type'],
                'credits': user.get('credits', 0),
                'already_checked': already_checked,
            },
        }

    def get_user_info(self, current_user: dict) -> dict:
        if not self._db:
            return {'success': False, 'message': '获取用户信息失败：数据库服务不可用', '_http_status': 500}
        user_data = self._db.get_user_by_username(current_user['username'])
        if not user_data:
            return {'success': False, 'message': '用户数据异常，请重新登录', '_http_status': 401}
        return {
            'success': True,
            '_http_status': 200,
            'user': {
                'username': user_data['username'],
                'email': user_data['email'],
                'user_type': user_data['user_type'],
                'daily_predictions_used': user_data['daily_predictions_used'],
                'total_predictions': user_data['total_predictions'],
                'membership_expires': user_data['membership_expires'].isoformat()
                if user_data.get('membership_expires')
                else None,
            },
        }

    def can_predict(self, current_user: dict) -> dict:
        if not self._db:
            return {'success': False, 'message': '检查失败：数据库服务不可用', '_http_status': 500}
        user_data = self._db.get_user_by_username(current_user['username'])
        if not user_data:
            return {'success': False, 'message': '用户数据异常，请重新登录', 'can_predict': False, '_http_status': 401}
        can_predict = self._db.can_user_predict(
            user_data['id'], user_data['user_type'], user_data['daily_predictions_used']
        )
        remaining = max(0, 3 - user_data['daily_predictions_used']) if user_data['user_type'] == 'free' else 0
        return {
            'success': True,
            '_http_status': 200,
            'can_predict': can_predict,
            'user_type': user_data['user_type'],
            'daily_used': user_data['daily_predictions_used'],
            'remaining': remaining,
        }

    def get_credits(self, current_user: dict) -> dict:
        if not self._db:
            return {'success': False, 'credits': 0, 'message': '未登录', '_http_status': 401}
        credits = self._db.get_user_credits(current_user['id'])
        return {'success': True, 'credits': credits, '_http_status': 200}

    def checkin(self, current_user: dict) -> dict:
        if not self._db:
            return {'success': False, 'message': '请先登录', '_http_status': 401}
        result = self._db.checkin(current_user['id'], current_user['user_type'])
        result['_http_status'] = 200
        return result

    def get_ai_config(self, current_user: dict) -> dict:
        if not self._db:
            return {'success': False, 'message': '请先登录', '_http_status': 401}
        config = self._db.get_user_ai_config(current_user['id'])
        if config.get('ai_engine_type') == 'system':
            config['ai_engine_type'] = 'api_key'
        masked_key = config.get('ai_api_key', '')
        if masked_key and len(masked_key) > 10:
            masked_key = masked_key[:6] + '***' + masked_key[-4:]
        elif masked_key:
            masked_key = '***'
        config['_masked_key'] = masked_key
        return {'success': True, 'config': config, '_http_status': 200}

    def save_ai_config(self, current_user: dict, data: dict) -> dict:
        if not self._db:
            return {'success': False, 'message': '请先登录', '_http_status': 401}
        engine_type = (data.get('ai_engine_type') or 'api_key').strip()
        if engine_type == 'system':
            engine_type = 'api_key'
        old = self._db.get_user_ai_config(current_user['id'])
        api_key = data.get('ai_api_key', '').strip()
        if api_key.startswith('***') or '***' in api_key:
            api_key = old.get('ai_api_key', '')
        config = {
            'ai_engine_type': engine_type,
            'ai_api_url': data.get('ai_api_url', '').strip(),
            'ai_api_key': api_key,
            'ai_model': data.get('ai_model', '').strip(),
            'cli_path_kiro': data.get('cli_path_kiro', 'kiro').strip(),
            'cli_path_antigravity': data.get('cli_path_antigravity', 'antigravity').strip(),
            'cli_path_cursor': data.get('cli_path_cursor', 'cursor').strip(),
        }
        if engine_type == 'api_key':
            if not config['ai_api_url'] or not config['ai_api_key'] or not config['ai_model']:
                return {
                    'success': False,
                    'message': 'custom API 模式：API 服务地址、API Key 和模型名称均不能为空',
                    '_http_status': 400,
                }
        elif engine_type in ('kiro_cli', 'antigravity_cli', 'cursor_cli'):
            cli_path_map = {
                'kiro_cli': config['cli_path_kiro'],
                'antigravity_cli': config['cli_path_antigravity'],
                'cursor_cli': config['cli_path_cursor'],
            }
            cli_name_map = {'kiro_cli': 'Kiro', 'antigravity_cli': 'Antigravity', 'cursor_cli': 'Cursor'}
            if not cli_path_map[engine_type]:
                return {
                    'success': False,
                    'message': f'{cli_name_map[engine_type]} CLI 可执行路径不能为空',
                    '_http_status': 400,
                }
            if not config['ai_model']:
                return {
                    'success': False,
                    'message': f'{names[engine_type]} CLI 模式：分析模型名称不能为空',
                    '_http_status': 400,
                }
        if self._db.save_user_ai_config(current_user['id'], config):
            return {'success': True, 'message': 'AI 配置保存成功', '_http_status': 200}
        return {'success': False, 'message': '保存 AI 配置失败，请检查数据库连接', '_http_status': 500}

    def test_ai_config(self, current_user: dict, data: dict) -> dict:
        if not AIFootballPredictor:
            return {'success': False, 'message': 'AI 预测器不可用', '_http_status': 500}
        engine_type = (data.get('ai_engine_type') or 'api_key').strip()
        if engine_type == 'system':
            engine_type = 'api_key'
        old = self._db.get_user_ai_config(current_user['id'])
        api_key = data.get('ai_api_key', '').strip()
        if api_key.startswith('***') or '***' in api_key:
            api_key = old.get('ai_api_key', '')
        config = {
            'ai_engine_type': engine_type,
            'ai_api_url': data.get('ai_api_url', '').strip(),
            'ai_api_key': api_key,
            'ai_model': data.get('ai_model', '').strip(),
            'cli_path_kiro': data.get('cli_path_kiro', 'kiro').strip(),
            'cli_path_antigravity': data.get('cli_path_antigravity', 'antigravity').strip(),
            'cli_path_cursor': data.get('cli_path_cursor', 'cursor').strip(),
        }
        if engine_type == 'api_key':
            if not config['ai_api_url'] or not config['ai_api_key'] or not config['ai_model']:
                return {
                    'success': False,
                    'message': '使用自定义 API Key 时，服务地址、密钥和模型名称均不能为空',
                    '_http_status': 400,
                }
        predictor = AIFootballPredictor(
            user_id=current_user['id'],
            override_engine=engine_type,
            override_model=config.get('ai_model'),
        )
        predictor.engine_type = engine_type
        if engine_type == 'api_key':
            predictor.api_key = config['ai_api_key']
            predictor.base_url = config['ai_api_url'].rstrip('/')
            predictor.model_name = config['ai_model']
            predictor.use_openai_format = True
        elif engine_type == 'kiro_cli':
            predictor.cli_path_kiro = config['cli_path_kiro']
        elif engine_type == 'antigravity_cli':
            predictor.cli_path_antigravity = config['cli_path_antigravity']
        elif engine_type == 'cursor_cli':
            predictor.cli_path_cursor = config['cli_path_cursor']
        if engine_type in ('kiro_cli', 'antigravity_cli', 'cursor_cli'):
            predictor.api_key = config['ai_api_key']
            predictor.base_url = config['ai_api_url'].rstrip('/') if config['ai_api_url'] else ''
            predictor.model_name = config['ai_model']
        try:
            response = predictor._call_ai_model("请回复 'OK'，不需要任何其他多余文本。")
            if response:
                return {'success': True, 'message': '连接测试成功！', 'response': response, '_http_status': 200}
            return {'success': False, 'message': '连接成功，但 AI 未返回任何内容。', '_http_status': 200}
        except Exception as e:
            return {'success': False, 'message': str(e), '_http_status': 500}

    def login_cli_sync(self, current_user: dict, engine_type: str, cli_path: str,
                         api_key: str, api_url: str, api_model: str) -> dict:
        if not cli_path:
            return {'success': False, 'message': 'CLI 类型与命令路径不能为空', '_http_status': 400}
        try:
            cmd_args = shlex.split(cli_path)
        except Exception as e:
            return {'success': False, 'message': f'命令格式非法: {e}', '_http_status': 400}
        env = os.environ.copy()
        if api_key:
            env['GEMINI_API_KEY'] = api_key
            env['OPENAI_API_KEY'] = api_key
            env['CURSOR_TOKEN'] = api_key
        if api_url:
            env['GEMINI_API_URL'] = api_url
            env['GEMINI_BASE_URL'] = api_url
            env['OPENAI_API_BASE'] = api_url
        if api_model:
            env['GEMINI_MODEL'] = api_model
            env['OPENAI_MODEL'] = api_model
            env['AI_MODEL'] = api_model

        check_cmd = cmd_args + ['--help']
        is_antigravity = engine_type == 'antigravity_cli'
        if is_antigravity or not api_key:
            try:
                res = subprocess.run(
                    check_cmd, capture_output=True, text=True,
                    encoding='utf-8', errors='replace', env=env, timeout=8,
                )
                merged = f"STDOUT:\n{res.stdout.strip()}\n\nSTDERR:\n{res.stderr.strip()}"
                if res.returncode == 0:
                    engine_name = (
                        'Antigravity CLI' if is_antigravity
                        else ('Cursor CLI' if engine_type == 'cursor_cli' else 'Kiro CLI')
                    )
                    key_tip = (
                        '（已跳过登录，模型凭证将以环境变量注入）' if not api_key
                        else '（无需额外登录认证步骤）'
                    )
                    return {
                        'success': True,
                        'message': f'{engine_name} 路径校验成功并已就绪！{key_tip}',
                        'output': merged,
                        '_http_status': 200,
                    }
                return {
                    'success': False,
                    'message': f'CLI 路径可执行性校验失败，进程退出码: {res.returncode}',
                    'output': merged,
                    '_http_status': 400,
                }
            except FileNotFoundError:
                return {
                    'success': False,
                    'message': f"未在系统中找到可执行命令或绝对路径 '{cli_path}'",
                    '_http_status': 404,
                }
            except Exception as e:
                return {'success': False, 'message': f'执行登录认证时发生异常：{e}', '_http_status': 500}

        login_cmd = cmd_args + (['auth', 'login'] if engine_type == 'cursor_cli' else ['login'])
        try:
            res = subprocess.run(
                login_cmd, input=api_key or '', capture_output=True, text=True,
                encoding='utf-8', env=env, timeout=4,
            )
            merged = f"STDOUT:\n{res.stdout.strip()}\n\nSTDERR:\n{res.stderr.strip()}"
            if res.returncode == 0:
                return {'success': True, 'sync': True, 'message': 'CLI 登录指令执行完毕，返回状态正常。', 'output': merged, '_http_status': 200}
            fb = subprocess.run(cmd_args + ['--help'], capture_output=True, text=True, encoding='utf-8', env=env, timeout=4)
            if fb.returncode == 0:
                return {
                    'success': True,
                    'sync': True,
                    'message': 'CLI 登录返回了非零代码，但已通过 --help 校验路径。',
                    'output': f"登录返回:\n{merged}\n\npath校验:\n{fb.stdout.strip()}",
                    '_http_status': 200,
                }
            return {'success': False, 'sync': True, 'message': f'CLI 登录失败，退出码: {res.returncode}', 'output': merged, '_http_status': 400}
        except subprocess.TimeoutExpired:
            fb = subprocess.run(cmd_args + ['--help'], capture_output=True, text=True, encoding='utf-8', env=env, timeout=4)
            if fb.returncode == 0:
                return {
                    'success': True,
                    'sync': True,
                    'message': 'CLI 登录接口超时，但路径已通过 --help 校验。',
                    'output': fb.stdout.strip(),
                    '_http_status': 200,
                }
            return {'success': False, 'sync': True, 'message': 'CLI 登录执行超时', '_http_status': 408}
        except FileNotFoundError:
            return {'success': False, 'sync': True, 'message': f"未找到 '{cli_path}'", '_http_status': 404}
        except Exception as e:
            return {'success': False, 'sync': True, 'message': str(e), '_http_status': 500}

    def start_cli_login_job(self, engine_type: str, cli_path: str) -> str:
        job_id = str(uuid.uuid4())
        env = os.environ.copy()
        if engine_type == 'cursor_cli':
            login_cmd = shlex.split(cli_path) + ['login']
        else:
            login_cmd = shlex.split(cli_path) + ['login']
        _cli_login_jobs[job_id] = {
            'status': 'pending',
            'message': '正在启动 CLI 登录，浏览器将自动打开...',
            'output': '',
            'engine_type': engine_type,
            'started_at': time.time(),
        }
        threading.Thread(
            target=_run_cli_login_job_thread,
            args=(job_id, login_cmd, env, engine_type),
            daemon=True,
        ).start()
        return job_id

    def poll_cli_login(self, job_id: str) -> dict:
        job = _cli_login_jobs.get(job_id)
        if not job:
            return {
                'success': False,
                'job_status': 'not_found',
                'message': '任务不存在或已过期',
                '_http_status': 404,
            }
        elapsed = int(time.time() - job.get('started_at', time.time()))
        return {
            'success': True,
            'status': job['status'],
            'message': job['message'],
            'output': job.get('output', ''),
            'elapsed': elapsed,
            '_http_status': 200,
        }

    def cli_logout(self, cli_path: str, engine_type: str) -> dict:
        if not cli_path:
            return {'success': False, 'message': 'CLI 路径不能为空', '_http_status': 400}
        try:
            cmd_args = shlex.split(cli_path)
        except Exception as e:
            return {'success': False, 'message': f'命令格式非法: {e}', '_http_status': 400}
        logout_cmd = cmd_args + (['logout'] if engine_type == 'cursor_cli' else ['logout'])
        env = os.environ.copy()
        try:
            res = subprocess.run(
                logout_cmd, capture_output=True, text=True, encoding='utf-8',
                errors='replace', env=env, timeout=15,
            )
            merged = (res.stdout.strip() + '\n' + res.stderr.strip()).strip()
            return {'success': True, 'message': '退出登录成功！现在可以重新登录。', 'output': merged, '_http_status': 200}
        except FileNotFoundError:
            return {'success': False, 'message': f"未找到 '{cli_path}'", '_http_status': 404}
        except subprocess.TimeoutExpired:
            return {'success': False, 'message': '退出登录超时，请手动执行退出命令。', '_http_status': 408}
        except Exception as e:
            return {'success': False, 'message': str(e), '_http_status': 500}

    def resolve_masked_api_key(self, user_id: int, api_key: str) -> str:
        if api_key.startswith('***') or '***' in api_key:
            old = self._db.get_user_ai_config(user_id)
            return old.get('ai_api_key', '')
        return api_key.strip()

    def detect_cli_models(self, current_user: dict, cli_path: str, engine_type: str) -> dict:
        if not cli_path:
            return {'success': False, 'message': 'CLI 路径不能为空', '_http_status': 400}
        try:
            cmd_args = shlex.split(cli_path)
        except Exception as e:
            return {'success': False, 'message': f'命令格式非法: {e}', '_http_status': 400}

        model_cmds = {
            'kiro_cli': [['models'], ['model', 'list'], ['list', 'models']],
            'antigravity_cli': [['models'], ['model', 'list'], ['list']],
            'cursor_cli': [['models'], ['model', 'list'], ['api', 'models']],
        }
        candidates = model_cmds.get(engine_type, [['models']])
        env = os.environ.copy()
        old_config = self._db.get_user_ai_config(current_user['id'])
        if old_config.get('ai_api_key'):
            env['GEMINI_API_KEY'] = old_config['ai_api_key']
            env['OPENAI_API_KEY'] = old_config['ai_api_key']
            env['CURSOR_TOKEN'] = old_config['ai_api_key']
        if old_config.get('ai_api_url'):
            env['OPENAI_API_BASE'] = old_config['ai_api_url']
            env['GEMINI_API_URL'] = old_config['ai_api_url']

        preset_models = {
            'kiro_cli': [
                'claude-sonnet-4-5', 'claude-sonnet-4-5-20251101', 'claude-opus-4',
                'claude-3-7-sonnet-latest', 'claude-3-5-sonnet-20241022', 'gpt-4o', 'gpt-4o-mini',
            ],
            'cursor_cli': [
                'claude-sonnet-4-5', 'claude-sonnet-4-5-thinking', 'claude-opus-4',
                'gpt-4o', 'gpt-4o-mini', 'gpt-5', 'gemini-2.5-pro', 'cursor-small',
            ],
            'antigravity_cli': [
                'gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro',
                'claude-sonnet-4-5', 'claude-opus-4', 'gpt-4o',
            ],
        }
        error_keywords = ['error', 'unrecognized', 'unknown', 'not found', 'no models available']
        last_output = ''
        for sub in candidates:
            try:
                res = subprocess.run(
                    cmd_args + sub, capture_output=True, text=True,
                    encoding='utf-8', env=env, timeout=10,
                )
                combined = (res.stdout + res.stderr).strip()
                last_output = combined
                combined_lower = combined.lower()
                if any(kw in combined_lower for kw in error_keywords):
                    continue
                if res.returncode == 0 and combined:
                    lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]
                    models = [
                        ln for ln in lines
                        if ln and len(ln) < 80 and not ln.startswith('#') and not ln.startswith('-')
                    ]
                    if models:
                        return {
                            'success': True,
                            'models': models,
                            'raw': combined,
                            'source': 'detected',
                            '_http_status': 200,
                        }
            except (FileNotFoundError, subprocess.TimeoutExpired):
                break
            except Exception:
                continue

        presets = preset_models.get(engine_type, [])
        if presets:
            return {
                'success': True,
                'models': presets,
                'message': '已返回该 CLI 引擎的推荐模型列表（自动检测不可用，以下为已知可用模型）。',
                'source': 'preset',
                '_http_status': 200,
            }
        return {
            'success': False,
            'message': '未能自动检测到模型列表，请手动填写模型名称。',
            'raw': last_output,
            '_http_status': 200,
        }

    def start_cli_login(self, engine_type: str, cli_path: str) -> dict:
        if not engine_type or not cli_path:
            return {'success': False, 'message': 'CLI 类型与命令路径不能为空', '_http_status': 400}
        try:
            cmd_args = shlex.split(cli_path)
        except Exception as e:
            return {'success': False, 'message': f'命令格式非法: {e}', '_http_status': 400}

        env = os.environ.copy()
        if engine_type == 'antigravity_cli':
            try:
                res = subprocess.run(
                    cmd_args + ['--help'], capture_output=True, text=True,
                    encoding='utf-8', errors='replace', env=env, timeout=8,
                )
                merged = f"STDOUT:\n{res.stdout.strip()}\n\nSTDERR:\n{res.stderr.strip()}"
                if res.returncode == 0:
                    return {
                        'success': True,
                        'sync': True,
                        'message': 'Antigravity CLI 路径校验成功！无需 OAuth 登录，凭证将在预测时通过环境变量注入。',
                        'output': merged,
                        '_http_status': 200,
                    }
                return {
                    'success': False,
                    'sync': True,
                    'message': f'Antigravity CLI 路径校验失败，退出码: {res.returncode}',
                    'output': merged,
                    '_http_status': 400,
                }
            except FileNotFoundError:
                return {'success': False, 'sync': True, 'message': f"未找到 '{cli_path}'，请确认已安装。", '_http_status': 404}
            except Exception as e:
                return {'success': False, 'sync': True, 'message': str(e), '_http_status': 500}

        job_id = self.start_cli_login_job(engine_type, cli_path)
        return {
            'success': True,
            'sync': False,
            'job_id': job_id,
            'message': '后台登录任务已启动，浏览器将自动打开 OAuth 授权页面...',
            '_http_status': 200,
        }


def _run_cli_login_job_thread(job_id: str, cmd_args: list, env: dict, engine_type: str):
    url_pattern = re.compile(r'https?://\S+')
    browser_opened = False
    output_lines = []
    try:
        proc = subprocess.Popen(
            cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', env=env,
        )
        deadline = time.time() + 180
        while True:
            if time.time() > deadline:
                proc.kill()
                _cli_login_jobs[job_id].update({
                    'status': 'failed',
                    'message': '登录等待超时（超过 3 分钟），请重试。',
                    'output': '\n'.join(output_lines),
                })
                return
            line = proc.stdout.readline()
            if line == '' and proc.poll() is not None:
                break
            if line:
                output_lines.append(line.rstrip())
                _cli_login_jobs[job_id]['output'] = '\n'.join(output_lines[-40:])
                if not browser_opened:
                    for url in url_pattern.findall(line):
                        if any(kw in url for kw in ['login', 'auth', 'oauth', 'token', 'signin', 'sso', 'code=']):
                            try:
                                subprocess.Popen(['open', url])
                                browser_opened = True
                                _cli_login_jobs[job_id]['browser_opened'] = True
                                _cli_login_jobs[job_id]['message'] = f'浏览器已打开授权页面，请完成登录...\n🔗 {url}'
                            except Exception:
                                pass
                            break
        full_output = '\n'.join(output_lines)
        already = any(kw in full_output.lower() for kw in ['already logged in', 'already signed in', 'already authenticated'])
        if proc.wait() == 0:
            _cli_login_jobs[job_id].update({
                'status': 'success',
                'message': 'CLI 登录授权成功！凭证已保存到本地。',
                'output': full_output,
            })
        elif already:
            _cli_login_jobs[job_id].update({
                'status': 'already_logged_in',
                'message': '该 CLI 当前已处于登录状态。您可以直接使用，或先退出再重新登录。',
                'output': full_output,
                'cmd_args': cmd_args,
            })
        else:
            _cli_login_jobs[job_id].update({
                'status': 'failed',
                'message': f'CLI 登录失败，退出码: {proc.wait()}',
                'output': full_output,
            })
    except FileNotFoundError:
        _cli_login_jobs[job_id].update({
            'status': 'failed',
            'message': '未找到 CLI 可执行文件，请确认路径正确并已安装。',
            'output': '',
        })
    except Exception as e:
        _cli_login_jobs[job_id].update({
            'status': 'failed',
            'message': f'登录过程发生异常：{e}',
            'output': '\n'.join(output_lines),
        })


auth_service = AuthService()