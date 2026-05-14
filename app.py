from flask import Flask, request, jsonify, render_template, session, make_response
import os
import json
import logging
import requests
import hashlib
import psycopg2
from datetime import datetime, timedelta

# 尝试导入数据库模块
try:
    from scripts.database import prediction_db
    print("✅ 数据库模块导入成功")
except ImportError as e:
    print(f"⚠️ 数据库模块导入失败: {e}")
    prediction_db = None

try:
    from scripts.lottery_api import ChinaSportsLotterySpider
except ImportError:
    try:
        from lottery_api import ChinaSportsLotterySpider
    except ImportError as e:
        print(f"导入彩票API失败: {e}")
        ChinaSportsLotterySpider = None

try:
    from scripts.ai_predictor import AIFootballPredictor
except ImportError:
    try:
        from ai_predictor import AIFootballPredictor
    except ImportError as e:
        print(f"导入AI预测器失败: {e}")
        AIFootballPredictor = None

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production') # 在生产环境中务必设置一个强随机 SECRET_KEY
# Session/Cookie 配置，确保登录态可用
# 检测是否为本地开发环境（HTTP）
_IS_LOCAL = os.environ.get('FLASK_ENV', 'production') == 'development' \
    or os.environ.get('FLASK_DEBUG', '0') == '1' \
    or os.environ.get('LOCAL_DEV', '0') == '1'

app.config.update(
    SESSION_COOKIE_NAME='mp_session',
    # 本地 HTTP 开发：SameSite=Lax, Secure=False（浏览器允许 HTTP Cookie）
    # 生产 HTTPS 环境：SameSite=None, Secure=True（支持跨站请求）
    SESSION_COOKIE_SAMESITE='Lax' if _IS_LOCAL else os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax'),
    SESSION_COOKIE_SECURE=False if _IS_LOCAL else True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_DOMAIN=None if _IS_LOCAL else os.environ.get('SESSION_COOKIE_DOMAIN'),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)

# 配置日志
logging.basicConfig(level=logging.INFO)

# 全局变量
lottery_spider = None
ai_predictor = None

# 联赛配置（简化版）
LEAGUES = {
    "PL": "英超",
    "PD": "西甲", 
    "SA": "意甲",
    "BL1": "德甲",
    "FL1": "法甲"
}

# 简化的球队数据
TEAMS_DATA = {
    "PL": ["Arsenal FC", "Manchester City FC", "Liverpool FC", "Manchester United FC", "Chelsea FC", "Tottenham Hotspur FC", "Newcastle United FC", "Brighton & Hove Albion FC"],
    "PD": ["Real Madrid CF", "FC Barcelona", "Atlético de Madrid", "Sevilla FC", "Valencia CF", "Real Betis Balompié", "Real Sociedad de Fútbol", "Athletic Club"],
    "SA": ["FC Internazionale Milano", "AC Milan", "Juventus FC", "SSC Napoli", "AS Roma", "SS Lazio", "Atalanta BC", "ACF Fiorentina"],
    "BL1": ["FC Bayern München", "Borussia Dortmund", "RB Leipzig", "Bayer 04 Leverkusen", "VfB Stuttgart", "Eintracht Frankfurt", "VfL Wolfsburg", "SC Freiburg"],
    "FL1": ["Paris Saint-Germain FC", "Olympique de Marseille", "AS Monaco FC", "Olympique Lyonnais", "OGC Nice", "Stade Rennais FC", "RC Lens", "LOSC Lille"]
}

def initialize_services():
    """初始化服务"""
    global lottery_spider, ai_predictor
    
    try:
        # 初始化中国体育彩票API
        if ChinaSportsLotterySpider:
            lottery_spider = ChinaSportsLotterySpider()
            app.logger.info("彩票API初始化成功")
        else:
            app.logger.warning("彩票API类未加载")
    except Exception as e:
        app.logger.error(f"彩票API初始化失败: {e}")
        lottery_spider = None
    
    try:
        # 初始化AI预测器
        if AIFootballPredictor:
            gemini_api_key = os.environ.get('GEMINI_API_KEY')
            gemini_model = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-06-17')
            
            if not gemini_api_key:
                app.logger.warning("GEMINI_API_KEY环境变量未设置，AI预测器将不可用")
                ai_predictor = None
            else:
                ai_predictor = AIFootballPredictor(
                    api_key=gemini_api_key,
                    model_name=gemini_model
                )
                app.logger.info("AI预测器初始化成功")
        else:
            app.logger.warning("AI预测器类未加载")
    except Exception as e:
        app.logger.error(f"AI预测器初始化失败: {e}")
        ai_predictor = None

# 初始化
try:
    initialize_services()
except Exception as e:
    app.logger.error(f"服务初始化失败: {e}")

# 用户认证相关辅助函数
def hash_password(password):
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

# 移除了 simple_create_user_db 函数，因为 prediction_db.create_user 已经足够健壮。

def get_current_user():
    """获取当前登录用户"""
    if 'user_id' in session:
        return prediction_db.get_user_by_username(session['username']) if prediction_db else None
    return None

def require_login():
    """检查是否需要登录"""
    return get_current_user() is None

@app.after_request
def add_cors_headers(response):
    """为需要的接口添加基础CORS支持，避免OPTIONS 405。"""
    origin = request.headers.get('Origin')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
        app.logger.debug(f"为请求 {request.path} 添加了CORS头部，Origin: {origin}")
    if request.method == 'OPTIONS':
        response.status_code = 204
        app.logger.debug(f"处理OPTIONS请求: {request.path}")
    return response

@app.route('/api/session/debug')
def session_debug():
    """调试用：查看当前会话是否存在。上线可移除。"""
    return jsonify({
        'logged_in': 'user_id' in session,
        'user_id': session.get('user_id'),
        'username': session.get('username')
    })

@app.route('/')
def index():
    try:
        # 将环境变量传递给前端
        gemini_api_key = os.environ.get('GEMINI_API_KEY', '')
        gemini_model = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-06-17')
        
        # 获取当前用户信息
        current_user = get_current_user()
        
        return render_template('index.html', 
                             gemini_api_key=gemini_api_key,
                             gemini_model=gemini_model,
                             current_user=current_user)
    except Exception as e:
        app.logger.error(f"渲染主页失败: {e}")
        return f"页面加载错误: {str(e)}", 500

@app.route('/api/teams')
def get_teams():
    """获取球队数据"""
    try:
        # 返回简化的球队数据
        teams = {
            "PL": ["Arsenal FC", "Manchester City FC", "Liverpool FC", "Manchester United FC", 
                   "Chelsea FC", "Tottenham Hotspur FC", "Newcastle United FC", "Brighton & Hove Albion FC"],
            "PD": ["Real Madrid CF", "FC Barcelona", "Atlético de Madrid", "Sevilla FC", 
                   "Valencia CF", "Real Betis Balompié", "Real Sociedad", "Athletic Bilbao"],
            "SA": ["FC Internazionale Milano", "AC Milan", "Juventus FC", "SSC Napoli", 
                   "AS Roma", "SS Lazio", "Atalanta BC", "ACF Fiorentina"],
            "BL1": ["FC Bayern München", "Borussia Dortmund", "RB Leipzig", "Bayer 04 Leverkusen", 
                    "VfB Stuttgart", "Eintracht Frankfurt", "Borussia Mönchengladbach", "VfL Wolfsburg"],
            "FL1": ["Paris Saint-Germain FC", "Olympique de Marseille", "AS Monaco FC", "Olympique Lyonnais", 
                    "OGC Nice", "Stade Rennais FC", "RC Lens", "RC Strasbourg Alsace"]
        }
        
        return jsonify({
            'success': True,
            'teams': teams,
            'message': '球队数据获取成功'
        })
        
    except Exception as e:
        app.logger.error(f"获取球队数据失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': '获取球队数据失败'
        }), 500

@app.route('/api/lottery/matches')
def get_lottery_matches():
    """获取中国体育彩票比赛数据 - 仅从数据库获取"""
    try:
        days = request.args.get('days', 3, type=int)
        days = min(max(days, 1), 7)  # 限制在1-7天之间
        
        app.logger.info(f"📊 从数据库获取体彩数据 - 天数: {days}")
        
        if not prediction_db:
            app.logger.error("❌ 数据库未初始化")
            return jsonify({
                'success': False,
                'error': '数据库未配置',
                'message': '数据库连接失败，请联系管理员'
            }), 500
        
        try:
            # 仅从数据库获取
            db_matches = prediction_db.get_daily_matches(days_ahead=days)
            
            if db_matches and len(db_matches) > 0:
                app.logger.info(f"✅ 从数据库获取到 {len(db_matches)} 场比赛")
                
                return jsonify({
                    'success': True,
                    'matches': db_matches,
                    'count': len(db_matches),
                    'message': f'从数据库获取 {len(db_matches)} 场比赛',
                    'source': 'database'
                })
            else:
                app.logger.warning("⚠️ 数据库中没有找到比赛数据")
                
                return jsonify({
                    'success': False,
                    'error': '暂无比赛数据',
                    'message': '数据库中暂无比赛数据，请运行同步脚本更新数据：python scripts/sync_daily_matches.py --days 7'
                }), 404
                
        except Exception as db_error:
            app.logger.error(f"❌ 数据库获取失败: {db_error}")
            
            return jsonify({
                'success': False,
                'error': str(db_error),
                'message': '数据库查询失败，请稍后重试'
            }), 500
            
    except Exception as e:
        app.logger.error(f"❌ 获取体彩数据失败: {e}")
        
        return jsonify({
            'success': False,
            'error': str(e),
            'message': '系统错误，暂时无法获取数据'
        }), 500

@app.route('/api/save-prediction', methods=['POST'])
def save_prediction():
    """保存预测结果到数据库"""
    try:
        if not prediction_db:
            return jsonify({
                'success': False,
                'message': '数据库未配置'
            }), 500
        
        # 检查用户登录状态
        current_user = get_current_user()
        if not current_user:
            return jsonify({
                'success': False,
                'message': '请先登录再进行预测'
            }), 401
        
        # 检查用户预测权限
        can_predict = prediction_db.can_user_predict(
            current_user['id'], 
            current_user['user_type'], 
            current_user['daily_predictions_used']
        )
        
        if not can_predict:
            return jsonify({
                'success': False,
                'message': '今日免费预测次数已用完，请升级会员'
            }), 403

        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': '请求数据为空'
            }), 400
        
        prediction_mode = data.get('mode', '').lower()
        match_data = data.get('match_data', {})
        prediction_result = data.get('prediction_result', '')
        confidence = data.get('confidence', 0)
        ai_analysis = data.get('ai_analysis', '')
        user_ip = request.remote_addr
        
        success = False
        
        if prediction_mode == 'ai':
            success = prediction_db.save_ai_prediction(
                match_data=match_data,
                prediction_result=prediction_result,
                confidence=confidence,
                ai_analysis=ai_analysis,
                user_ip=user_ip,
                user_id=current_user['id'],
                username=current_user['username']
            )
        elif prediction_mode == 'classic':
            success = prediction_db.save_classic_prediction(
                match_data=match_data,
                prediction_result=prediction_result,
                confidence=confidence,
                user_ip=user_ip,
                user_id=current_user['id'],
                username=current_user['username']
            )
        elif prediction_mode == 'lottery':
            success = prediction_db.save_lottery_prediction(
                match_data=match_data,
                prediction_result=prediction_result,
                confidence=confidence,
                ai_analysis=ai_analysis,
                user_ip=user_ip,
                user_id=current_user['id'],
                username=current_user['username']
            )
        else:
            return jsonify({
                'success': False,
                'message': '未知的预测模式'
            }), 400
        
        if success:
            # 增加用户预测次数
            prediction_db.increment_user_predictions(current_user['id'])
            
            # 重新从数据库获取最新用户数据，包括更新后的预测次数
            updated_user = prediction_db.get_user_by_username(current_user['username'])

            # 如果成功获取到更新的用户数据，则更新session并返回
            if updated_user:
                session['user_id'] = updated_user['id']
                session['username'] = updated_user['username']
                session.permanent = True
                app.logger.info(f"用户 {updated_user['username']} 预测次数已更新: {updated_user['daily_predictions_used']}")
                return jsonify({
                    'success': True,
                    'message': '预测结果保存成功',
                    'user': {
                        'username': updated_user['username'],
                        'user_type': updated_user['user_type'],
                        'daily_predictions_used': updated_user['daily_predictions_used'],
                        'total_predictions': updated_user['total_predictions'],
                        'membership_expires': updated_user['membership_expires'].isoformat() if updated_user['membership_expires'] else None
                    }
                })
            else:
                app.logger.error(f"保存预测后无法获取更新后的用户数据: {current_user['username']}", exc_info=True)
                return jsonify({'success': False, 'message': '预测成功，但获取用户状态失败'}), 500
        else:
            return jsonify({
                'success': False,
                'message': '预测结果保存失败'
            }), 500
            
    except Exception as e:
        app.logger.error(f"保存预测结果失败: {e}")
        return jsonify({
            'success': False,
            'message': f'服务器错误: {str(e)}'
        }), 500

@app.route('/api/prediction-stats', methods=['GET'])
def get_prediction_stats():
    """获取预测统计信息"""
    try:
        if not prediction_db:
            return jsonify({
                'success': False,
                'message': '数据库未配置'
            }), 500
            
        stats = prediction_db.get_prediction_stats()
        return jsonify({
            'success': True,
            'data': stats
        })
        
    except Exception as e:
        app.logger.error(f"获取统计信息失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取统计信息失败: {str(e)}'
        }), 500

# ── 深度权重预测（Tab1: /api/analyze/classic）────────────────────
@app.route('/api/analyze/classic', methods=['POST'])
def analyze_classic():
    """深度权重预测 - 消耗1积分"""
    import math
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录', 'error_code': 'NOT_LOGGED_IN'}), 401
        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库服务不可用'}), 500
        COST = 1
        credits = prediction_db.get_user_credits(current_user['id'])
        if credits < COST:
            return jsonify({'success': False, 'error_code': 'INSUFFICIENT_CREDITS',
                'message': f'积分不足（需要{COST}积分，当前{credits}积分）',
                'cost': COST, 'current_credits': credits}), 402
        data = request.get_json() or {}
        matches = data.get('matches', [])
        if not matches:
            return jsonify({'success': False, 'message': '未提供比赛数据'}), 400
        prediction_db.deduct_credits(current_user['id'], COST)
        individual_predictions = []
        for m in matches:
            odds = (m.get('odds') or {}).get('hhad', {})
            ho = float(odds.get('h') or m.get('home_odds') or 2.0)
            do_ = float(odds.get('d') or m.get('draw_odds') or 3.2)
            ao = float(odds.get('a') or m.get('away_odds') or 3.5)
            home = m.get('home_team', '')
            away = m.get('away_team', '')
            league = m.get('league_code', '') or m.get('league_name', '')
            raw_h, raw_d, raw_a = 1/max(ho,1.01), 1/max(do_,1.01), 1/max(ao,1.01)
            total = raw_h + raw_d + raw_a
            ph, pd, pa = raw_h/total, raw_d/total, raw_a/total
            if ph >= pd and ph >= pa: rec = '主胜'
            elif pd >= ph and pd >= pa: rec = '平局'
            else: rec = '客胜'
            lam_h = max(-math.log(max(1-ph, 0.01)) * 1.5, 0.3)
            lam_a = max(-math.log(max(1-pa, 0.01)) * 1.5, 0.3)
            score_h = min(round(lam_h), 4)
            score_a = min(round(lam_a), 4)
            best_odds = ho if rec=='主胜' else (do_ if rec=='平局' else ao)
            best_prob = ph if rec=='主胜' else (pd if rec=='平局' else pa)
            individual_predictions.append({
                'home_team': home, 'away_team': away, 'league': league, 'mode': 'statistical',
                'probabilities': {'home': round(ph,4), 'draw': round(pd,4), 'away': round(pa,4)},
                'recommendation': rec,
                'score_prediction': f'{score_h}-{score_a}',
                'halftime_prediction': '主胜' if ph>0.45 else ('平局' if pd>0.35 else '客胜'),
                'halftime_score': f'{max(score_h-1,0)}-{max(score_a-1,0)}',
                'ht_ft_combo': f'{"主胜" if ph>0.45 else "平局"}/{"主胜" if ph>0.4 else "平局"}',
                'top_scores': [
                    {'score': f'{score_h}-{score_a}', 'prob': round(ph*35,1)},
                    {'score': f'{score_h+1}-{score_a}', 'prob': round(ph*18,1)},
                    {'score': f'{score_h}-{score_a+1}', 'prob': round(pa*22,1)},
                    {'score': '1-1', 'prob': round(pd*30,1)},
                    {'score': '0-0', 'prob': round(pd*18,1)},
                ],
                'expected_values': {'home': round(ph*ho-1,3), 'draw': round(pd*do_-1,3), 'away': round(pa*ao-1,3)},
                'best_bet': {'label': rec, 'odds': best_odds, 'ev': round(best_prob*best_odds-1, 3)},
            })
        credits_after = prediction_db.get_user_credits(current_user['id'])
        return jsonify({'success': True, 'individual_predictions': individual_predictions, 'credits_remaining': credits_after})
    except Exception as e:
        app.logger.error(f'深度权重预测失败: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/ai/predict', methods=['POST'])
def ai_predict():
    """AI大模型预测 - 鉴权+扣积分，返回 prompt+api_key 供浏览器调用 Gemini（Tab2）"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录', 'error_code': 'NOT_LOGGED_IN'}), 401
        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库服务不可用'}), 500

        data = request.get_json() or {}
        matches = data.get('matches', [])
        if not matches:
            return jsonify({'success': False, 'message': '未提供比赛数据'}), 400

        COST_PER_MATCH = 3
        total_cost = len(matches) * COST_PER_MATCH
        credits = prediction_db.get_user_credits(current_user['id'])
        if credits < total_cost:
            return jsonify({'success': False, 'error_code': 'INSUFFICIENT_CREDITS',
                'message': f'积分不足（需要{total_cost}积分，当前{credits}积分）',
                'cost': total_cost, 'current_credits': credits}), 402

        prediction_db.deduct_credits(current_user['id'], total_cost)

        gemini_key = os.environ.get('GEMINI_API_KEY', '')
        gemini_model = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash-exp')
        enriched = []
        for m in matches:
            home = m.get('home_team', '') or m.get('home', '')
            away = m.get('away_team', '') or m.get('away', '')
            league = m.get('league_name', '') or m.get('league', '')
            ho = m.get('home_odds') or (m.get('odds') or {}).get('h') or (m.get('odds') or {}).get('hhad', {}).get('h', '-')
            do_ = m.get('draw_odds') or (m.get('odds') or {}).get('d') or (m.get('odds') or {}).get('hhad', {}).get('d', '-')
            ao = m.get('away_odds') or (m.get('odds') or {}).get('a') or (m.get('odds') or {}).get('hhad', {}).get('a', '-')
            t = m.get('match_time', '') or m.get('match_date', '')
            prompt = f"""请对以下足球比赛进行深度分析预测（中文，800字以内）：

**比赛信息**
联赛：{league} | 比赛时间：{t}
主队：{home}  vs  客队：{away}
当前赔率：主胜 {ho} / 平局 {do_} / 客胜 {ao}

**分析维度**
1. 赔率解读：从赔率反推隐含概率，判断市场倾向
2. 球队分析：近期状态、主客场表现、历史交锋记录
3. 关键因素：伤病/停赛、积分压力、赛季阶段
4. 预测结论：明确推荐主胜/平局/客胜，并给出理由
5. 风险提示：指出不确定性因素

用清晰段落结构回答。"""
            enriched.append({**m, 'home_team': home, 'away_team': away, 'league_name': league,
                'home_odds': ho, 'draw_odds': do_, 'away_odds': ao, 'prompt': prompt, 'from_cache': False})

        return jsonify({'success': True, 'api_key': gemini_key, 'model': gemini_model,
            'matches': enriched, 'credits_deducted': total_cost})

    except Exception as e:
        app.logger.error(f'AI预测初始化失败: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/api/ai/save', methods=['POST'])
def ai_save():
    """保存 AI 分析结果（fire-and-forget，不影响主流程）"""
    try:
        current_user = get_current_user()
        data = request.get_json() or {}
        results = data.get('results', [])
        if not results or not prediction_db or not current_user:
            return jsonify({'success': True, 'saved': 0})
        saved = 0
        for r in results:
            try:
                if hasattr(prediction_db, 'save_prediction'):
                    prediction_db.save_prediction(
                        user_id=current_user['id'],
                        home_team=r.get('home_team', ''),
                        away_team=r.get('away_team', ''),
                        league=r.get('league_name', ''),
                        match_time=r.get('match_time', ''),
                        prediction_type='ai',
                        result=r.get('ai_analysis', ''),
                    )
                saved += 1
            except Exception:
                pass
        return jsonify({'success': True, 'saved': saved})
    except Exception:
        return jsonify({'success': True, 'saved': 0})

@app.route('/api/predict', methods=['POST'])
def predict():
    """简化版预测接口"""
    try:
        data = request.json
        matches = data.get('matches', [])
        
        if not matches:
            return jsonify({
                'success': False,
                'message': '未提供比赛数据'
            })
        
        # 记录用户输入
        log_user_prediction(matches)
        
        # 简化预测逻辑
        individual_predictions = []
        for match in matches:
            prediction = simple_predict_match(match)
            individual_predictions.append(prediction)
        
        return jsonify({
            'success': True,
            'individual_predictions': individual_predictions,
            'message': '简化预测模式，推荐使用AI智能预测获得更准确结果'
        })
        
    except Exception as e:
        app.logger.error(f"预测错误: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'预测过程中发生错误: {str(e)}'
        })

def simple_predict_match(match):
    """简化的比赛预测"""
    home_odds = float(match.get('home_odds', 2.0))
    draw_odds = float(match.get('draw_odds', 3.0))
    away_odds = float(match.get('away_odds', 2.5))
    
    # 基于赔率的简单概率计算
    home_prob = 1 / home_odds
    draw_prob = 1 / draw_odds
    away_prob = 1 / away_odds
    
    total_prob = home_prob + draw_prob + away_prob
    
    # 归一化概率
    home_prob /= total_prob
    draw_prob /= total_prob
    away_prob /= total_prob
    
    return {
        'match': f"{match['home_team']} vs {match['away_team']}",
        'home_team': match['home_team'],
        'away_team': match['away_team'],
        'probabilities': {
            'home': round(home_prob, 3),
            'draw': round(draw_prob, 3), 
            'away': round(away_prob, 3)
        },
        'odds': {
            'home': home_odds,
            'draw': draw_odds,
            'away': away_odds
        },
        'recommendation': '主胜' if home_prob > max(draw_prob, away_prob) else ('平局' if draw_prob > away_prob else '客胜')
    }

def generate_ai_combinations(ai_analyses):
    """基于AI分析生成组合预测"""
    combinations = []
    
    # 胜平负最佳组合
    best_wdl_combo = []
    total_confidence = 1.0
    
    for analysis in ai_analyses:
        wdl = analysis['win_draw_loss']
        best_outcome = max(wdl, key=wdl.get)
        
        best_wdl_combo.append({
            'match': f"{analysis['home_team']} vs {analysis['away_team']}",
            'prediction': best_outcome,
            'probability': wdl[best_outcome],
            'confidence': analysis['confidence_level']
        })
        
        total_confidence *= analysis['confidence_level']
    
    combinations.append({
        'type': '胜平负最佳组合',
        'selections': best_wdl_combo,
        'total_confidence': total_confidence,
        'description': '基于AI分析的最高概率胜平负组合'
    })
    
    return combinations

def log_user_prediction(matches):
    """记录用户预测请求"""
    try:
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'matches_count': len(matches),
            'matches': matches
        }
        
        # 简单的文件日志
        with open('user_predictions.log', 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
    except Exception as e:
        app.logger.error(f"记录用户预测失败: {str(e)}")

@app.route('/api/lottery/refresh', methods=['POST'])
def refresh_lottery_data():
    """刷新彩票数据"""
    try:
        data = request.json
        days = data.get('days', 3)
        
        if not lottery_spider:
            return jsonify({
                'success': False,
                'message': '彩票API未初始化'
            })
        
        matches = lottery_spider.get_formatted_matches(days)
        
        return jsonify({
            'success': True,
            'matches': matches,
            'count': len(matches)
        })
        
    except Exception as e:
        app.logger.error(f"刷新彩票数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'刷新数据失败: {str(e)}'
        })

@app.route('/api/ai/batch-predict', methods=['POST'])
def ai_batch_predict():
    """AI批量预测"""
    try:
        data = request.json
        matches = data.get('matches', [])
        
        if not matches:
            return jsonify({
                'success': False,
                'message': '未提供比赛数据'
            })
        
        # 调用AI预测
        return ai_predict()
        
    except Exception as e:
        app.logger.error(f"AI批量预测错误: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'批量预测失败: {str(e)}'
        })

@app.route('/test')
def test():
    """测试路由"""
    return jsonify({
        'status': 'ok',
        'message': '服务正常运行',
        'lottery_spider': lottery_spider is not None,
        'ai_predictor': ai_predictor is not None,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/sync/status')
def sync_status():
    """赛程同步状态 - 返回最后一次同步时间"""
    try:
        last_sync = None
        if prediction_db:
            with prediction_db.get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT MAX(updated_at) FROM upcoming_fixtures
                """)
                row = cur.fetchone()
                if row and row[0]:
                    # 格式化为北京时间
                    import pytz
                    cst = pytz.timezone('Asia/Shanghai')
                    dt = row[0]
                    if dt.tzinfo is None:
                        import pytz as _tz
                        dt = _tz.utc.localize(dt)
                    dt_cst = dt.astimezone(cst)
                    last_sync = dt_cst.strftime('%m-%d %H:%M')
        return jsonify({
            'success': True,
            'last_sync_time': last_sync,
            'message': f'上次同步: {last_sync}' if last_sync else '暂无同步记录'
        })
    except Exception as e:
        return jsonify({'success': True, 'last_sync_time': None, 'message': '状态获取失败'})


@app.route('/health')
def health():
    """健康检查"""
    return "OK", 200

@app.route('/data/<filename>')
def serve_data_files(filename):
    """提供数据文件访问"""
    try:
        from flask import send_from_directory
        return send_from_directory('data', filename)
    except Exception as e:
        app.logger.error(f"提供数据文件失败: {e}")
        return jsonify({'error': '文件未找到'}), 404

# 用户认证路由
@app.route('/api/register', methods=['POST', 'OPTIONS'])
def register():
    """用户注册"""
    app.logger.info(f"收到注册请求：{request.json}")
    try:
        if not prediction_db:
            app.logger.error("注册失败: 数据库未配置或初始化失败", exc_info=True)
            return jsonify({'success': False, 'message': '注册失败：数据库服务不可用'}), 500
            
        data = request.get_json()
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '')
        
        # 验证输入
        if not username or len(username) < 3:
            app.logger.warning(f"注册失败: 用户名不符合要求 - {username}")
            return jsonify({'success': False, 'message': '用户名长度至少3个字符'}), 400
        if not email or '@' not in email:
            app.logger.warning(f"注册失败: 邮箱格式无效 - {email}")
            return jsonify({'success': False, 'message': '请输入有效的邮箱地址'}), 400
        if not password or len(password) < 6:
            app.logger.warning(f"注册失败: 密码不符合要求 - {password}")
            return jsonify({'success': False, 'message': '密码长度至少6个字符'}), 400
        
        # 哈希密码
        password_hash = hash_password(password)
        
        # 创建用户
        success = prediction_db.create_user(username, email, password_hash)
        
        if success:
            app.logger.info(f"用户注册成功: {username}")
            resp = jsonify({'success': True, 'message': '注册成功，请登录'})
            # 调试用：设置一个临时测试 Cookie，帮助判断浏览器是否接受 SameSite=None; Secure
            try:
                pass # 移除调试Cookie设置
            except Exception as e:
                app.logger.warning(f"设置测试Cookie失败: {e}", exc_info=True)
                # 忽略设置 Cookie 时的任何异常
                pass
            return resp
        else:
            # create_user 内部已处理 UniqueViolation，这里捕获通用失败
            app.logger.warning(f"用户注册失败: 用户名或邮箱已存在或数据库操作失败 - {username}, {email}")
            return jsonify({'success': False, 'message': '注册失败：用户名或邮箱已存在，或数据库写入失败'}), 409
            
    except Exception as e:
        app.logger.error(f"用户注册失败（捕获到异常）: {e}", exc_info=True) # 打印完整堆栈
        return jsonify({'success': False, 'message': '注册失败，请稍后重试'}), 500

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    """用户登录"""
    app.logger.info(f"收到登录请求: {request.json}")
    try:
        if not prediction_db:
            app.logger.error("登录失败: 数据库未配置或初始化失败", exc_info=True)
            return jsonify({'success': False, 'message': '登录失败：数据库服务不可用'}), 500
            
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username or not password:
            app.logger.warning("登录失败: 缺少用户名或密码")
            return jsonify({'success': False, 'message': '请输入用户名和密码'}), 400
        
        # 哈希密码
        password_hash = hash_password(password)
        
        # 验证用户
        user = prediction_db.authenticate_user(username, password_hash)
        
        if user:
            # 设置session
            session['user_id'] = user['id']
            session['username'] = user['username']
            session.permanent = True
            
            app.logger.info(f"用户登录成功，设置会话: {username}")
            return jsonify({
                'success': True,
                'message': '登录成功',
                'user': {
                    'username': user['username'],
                    'user_type': user['user_type'],
                    'credits': user.get('credits', 0),
                }
            })
        else:
            app.logger.warning(f"用户登录失败: 用户名或密码错误 - {username}")
            return jsonify({'success': False, 'message': '用户名或密码错误'}), 401
            
    except Exception as e:
        app.logger.error(f"用户登录失败（捕获到异常）: {e}", exc_info=True) # 打印完整堆栈
        return jsonify({'success': False, 'message': '登录失败，请稍后重试'}), 500

@app.route('/api/logout', methods=['POST', 'OPTIONS'])
def logout():
    """用户登出"""
    session.clear()
    return jsonify({'success': True, 'message': '已安全退出'})

@app.route('/api/user/info', methods=['GET'])
def get_user_info():
    """获取用户信息"""
    app.logger.info("收到获取用户信息请求")
    try:
        current_user = get_current_user()
        if not current_user:
            app.logger.warning("获取用户信息失败: 用户未登录")
            return jsonify({'success': False, 'message': '未登录'}), 401
        
        # 确保 prediction_db 可用，get_current_user 已经做了初步检查
        if not prediction_db:
            app.logger.error("获取用户信息失败: 数据库未配置或初始化失败", exc_info=True)
            return jsonify({'success': False, 'message': '获取用户信息失败：数据库服务不可用'}), 500

        # 刷新用户数据以获取最新状态，特别是每日预测次数可能已重置
        user_data_from_db = prediction_db.get_user_by_username(current_user['username'])
        if not user_data_from_db:
            app.logger.error(f"获取用户信息失败: 数据库中未找到用户 {current_user['username']}", exc_info=True)
            # 用户可能已被删除，清理session
            session.clear()
            return jsonify({'success': False, 'message': '用户数据异常，请重新登录'}), 401

        app.logger.info(f"成功获取用户 {user_data_from_db['username']} 信息")
        return jsonify({
            'success': True,
            'user': {
                'username': user_data_from_db['username'],
                'email': user_data_from_db['email'],
                'user_type': user_data_from_db['user_type'],
                'daily_predictions_used': user_data_from_db['daily_predictions_used'],
                'total_predictions': user_data_from_db['total_predictions'],
                'membership_expires': user_data_from_db['membership_expires'].isoformat() if user_data_from_db['membership_expires'] else None
            }
        })
        
    except Exception as e:
        app.logger.error(f"获取用户信息失败（捕获到异常）: {e}", exc_info=True)
        return jsonify({'success': False, 'message': '获取用户信息失败'}), 500

@app.route('/api/user/can-predict', methods=['GET'])
def can_user_predict_api():
    """检查用户是否可以预测"""
    app.logger.info("收到检查用户预测权限请求")
    try:
        current_user = get_current_user()
        if not current_user:
            app.logger.warning("检查预测权限失败: 用户未登录")
            return jsonify({'success': False, 'message': '未登录', 'can_predict': False}), 401
        
        if not prediction_db:
            app.logger.error("检查预测权限失败: 数据库未配置或初始化失败", exc_info=True)
            return jsonify({'success': False, 'message': '检查失败：数据库服务不可用'}), 500

        user_data_from_db = prediction_db.get_user_by_username(current_user['username'])
        if not user_data_from_db:
            app.logger.error(f"检查预测权限失败: 数据库中未找到用户 {current_user['username']}", exc_info=True)
            session.clear()
            return jsonify({'success': False, 'message': '用户数据异常，请重新登录', 'can_predict': False}), 401

        can_predict = prediction_db.can_user_predict(
            user_data_from_db['id'], 
            user_data_from_db['user_type'], 
            user_data_from_db['daily_predictions_used']
        )
        
        remaining = 0
        if user_data_from_db['user_type'] == 'free':
            remaining = max(0, 3 - user_data_from_db['daily_predictions_used'])
        
        app.logger.info(f"用户 {user_data_from_db['username']} 预测权限检查结果: can_predict={can_predict}, remaining={remaining}")
        return jsonify({
            'success': True,
            'can_predict': can_predict,
            'user_type': user_data_from_db['user_type'],
            'daily_used': user_data_from_db['daily_predictions_used'],
            'remaining': remaining
        })
        
    except Exception as e:
        app.logger.error(f"检查预测权限失败（捕获到异常）: {e}", exc_info=True)
        return jsonify({'success': False, 'message': '检查失败'}), 500


# ── 积分 & 签到 ───────────────────────────────────────────────────────────────

@app.route('/api/user/credits', methods=['GET'])
def get_user_credits():
    """获取当前用户积分"""
    current_user = get_current_user()
    if not current_user or not prediction_db:
        return jsonify({'success': False, 'credits': 0, 'message': '未登录'}), 401
    credits = prediction_db.get_user_credits(current_user['id'])
    return jsonify({'success': True, 'credits': credits})


@app.route('/api/user/checkin', methods=['POST'])
def user_checkin():
    """每日签到"""
    current_user = get_current_user()
    if not current_user or not prediction_db:
        return jsonify({'success': False, 'message': '请先登录'}), 401
    result = prediction_db.checkin(current_user['id'], current_user['user_type'])
    return jsonify(result)


# ── 管理员初始化接口 ──────────────────────────────────────────────────────────

@app.route('/api/admin/init-db', methods=['POST'])
def admin_init_db():
    """
    一键初始化数据库表结构 + 超管账号。
    需在请求 Body 或 Header 携带 ADMIN_SECRET 密钥鉴权。

    Body JSON 参数（均可选）：
      secret   — 管理员密钥（必须与环境变量 ADMIN_SECRET 一致）
      username — 超管用户名，默认 'admin'
      email    — 超管邮箱，默认 'admin@matchpro.com'
      password — 初始密码，默认 'admin888'
    """
    try:
        data = request.get_json() or {}
        secret = data.get('secret') or request.headers.get('X-Admin-Secret', '')
        expected = os.environ.get('ADMIN_SECRET', '')

        if not expected:
            return jsonify({
                'success': False,
                'message': '服务器未配置 ADMIN_SECRET 环境变量，禁止操作'
            }), 403

        if secret != expected:
            app.logger.warning(f"非法的 init-db 请求，来源 IP: {request.remote_addr}")
            return jsonify({'success': False, 'message': '密钥错误，拒绝访问'}), 403

        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库未连接'}), 500

        # 1. 建表 + 注释 + 索引
        prediction_db.init_tables()

        # 2. 确保积分字段存在（兼容旧表）
        prediction_db.ensure_credits_columns()

        # 3. 初始化超管账号
        admin_result = prediction_db.init_admin(
            username=data.get('username', 'admin'),
            email=data.get('email', 'admin@matchpro.com'),
            password=data.get('password', 'admin888'),
        )

        app.logger.info(f"数据库初始化完成，超管: {admin_result}")
        return jsonify({
            'success': True,
            'message': '数据库初始化完成',
            'tables': ['users', 'match_predictions', 'daily_matches'],
            'admin': admin_result,
        })

    except Exception as e:
        app.logger.error(f"数据库初始化失败: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500



# ── 世界杯预测 ────────────────────────────────────────────────────────────────

# 16 强权重（FIFA排名 + 近期表现综合评分，越高越强）
WC_TEAM_WEIGHTS = {
    "阿根廷": 97, "法国": 95, "巴西": 93, "英格兰": 91,
    "西班牙": 90, "德国": 88, "葡萄牙": 87, "荷兰": 85,
    "意大利": 83, "比利时": 82, "克罗地亚": 80, "乌拉圭": 79,
    "哥伦比亚": 77, "美国": 75, "墨西哥": 74, "日本": 73,
}

def _wc_predict_match(home: str, away: str, ho: float = None, do_: float = None, ao: float = None):
    """基于权重计算世界杯单场结果"""
    import math, random
    w_h = WC_TEAM_WEIGHTS.get(home, 75)
    w_a = WC_TEAM_WEIGHTS.get(away, 75)

    # 主场优势 +5%
    w_h_adj = w_h * 1.05

    # 基础概率（Softmax）
    exp_h = math.exp(w_h_adj / 20)
    exp_d = math.exp((w_h_adj + w_a) / 45)
    exp_a = math.exp(w_a / 20)
    total = exp_h + exp_d + exp_a

    prob_h = exp_h / total
    prob_d = exp_d / total
    prob_a = exp_a / total

    # 如果有赔率，融合赔率隐含概率（权重 30%）
    if ho and do_ and ao:
        implied_h = 1 / ho
        implied_d = 1 / do_
        implied_a = 1 / ao
        s = implied_h + implied_d + implied_a
        implied_h /= s; implied_d /= s; implied_a /= s
        prob_h = prob_h * 0.7 + implied_h * 0.3
        prob_d = prob_d * 0.7 + implied_d * 0.3
        prob_a = prob_a * 0.7 + implied_a * 0.3

    # 预测比分（泊松均值）
    lambda_h = max(0.5, (w_h_adj / w_a) * 1.3)
    lambda_a = max(0.3, (w_a / w_h_adj) * 1.1)
    score_h = min(5, round(lambda_h * random.uniform(0.7, 1.3)))
    score_a = min(5, round(lambda_a * random.uniform(0.7, 1.3)))

    if prob_h >= prob_d and prob_h >= prob_a:
        rec = "主胜"
    elif prob_d >= prob_a:
        rec = "平局"
    else:
        rec = "客胜"

    return {
        "home_team": home,
        "away_team": away,
        "probabilities": {
            "home": round(prob_h, 3),
            "draw": round(prob_d, 3),
            "away": round(prob_a, 3),
        },
        "home_score_pred": score_h,
        "away_score_pred": score_a,
        "recommendation": rec,
    }


@app.route('/api/wc/predict', methods=['POST'])
def wc_predict():
    """世界杯单场预测（消耗1积分）"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录'}), 401

        # 扣除积分
        if prediction_db:
            ok = prediction_db.deduct_credits(current_user['id'], 1)
            if not ok:
                credits = prediction_db.get_user_credits(current_user['id'])
                return jsonify({
                    'success': False,
                    'error_code': 'INSUFFICIENT_CREDITS',
                    'message': '积分不足',
                    'credits': credits,
                    'cost': 1,
                }), 402

        data = request.get_json() or {}
        home = data.get('home_team', '')
        away = data.get('away_team', '')
        ho = float(data['ho']) if data.get('ho') else None
        do_ = float(data['do']) if data.get('do') else None
        ao = float(data['ao']) if data.get('ao') else None

        if not home or not away:
            return jsonify({'success': False, 'message': '请选择主队和客队'}), 400

        prediction = _wc_predict_match(home, away, ho, do_, ao)
        credits = prediction_db.get_user_credits(current_user['id']) if prediction_db else 0

        return jsonify({'success': True, 'prediction': prediction, 'credits': credits})

    except Exception as e:
        app.logger.error(f"世界杯预测失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/wc/simulate', methods=['POST'])
def wc_simulate():
    """淘汰赛全赛程模拟（消耗5积分）"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录'}), 401

        if prediction_db:
            ok = prediction_db.deduct_credits(current_user['id'], 5)
            if not ok:
                credits = prediction_db.get_user_credits(current_user['id'])
                return jsonify({
                    'success': False,
                    'error_code': 'INSUFFICIENT_CREDITS',
                    'message': '积分不足（本次需5分）',
                    'credits': credits,
                    'cost': 5,
                }), 402

        import random

        teams = list(WC_TEAM_WEIGHTS.keys())  # 16支
        random.shuffle(teams)

        def sim_match(home, away):
            p = _wc_predict_match(home, away)
            r = random.random()
            if r < p['probabilities']['home']:
                winner = home
            elif r < p['probabilities']['home'] + p['probabilities']['draw']:
                # 淘汰赛无平局，胜率高者晋级
                winner = home if p['probabilities']['home'] >= p['probabilities']['away'] else away
            else:
                winner = away
            return {
                'home': home, 'away': away,
                'score': f"{p['home_score_pred']}-{p['away_score_pred']}",
                'winner': winner,
            }

        # 1/8 决赛
        r16_matches = [sim_match(teams[i*2], teams[i*2+1]) for i in range(8)]
        r16_winners = [m['winner'] for m in r16_matches]

        # 1/4 决赛
        qf_matches = [sim_match(r16_winners[i*2], r16_winners[i*2+1]) for i in range(4)]
        qf_winners = [m['winner'] for m in qf_matches]

        # 半决赛
        sf_matches = [sim_match(qf_winners[0], qf_winners[1]), sim_match(qf_winners[2], qf_winners[3])]
        sf_winners = [m['winner'] for m in sf_matches]

        # 决赛
        final_match = sim_match(sf_winners[0], sf_winners[1])

        bracket = {
            'r16': r16_matches,
            'qf': qf_matches,
            'sf': sf_matches,
            'final': [final_match],
        }

        credits = prediction_db.get_user_credits(current_user['id']) if prediction_db else 0
        return jsonify({'success': True, 'bracket': bracket, 'credits': credits})

    except Exception as e:
        app.logger.error(f"世界杯模拟失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 新智能预测 API：未开赛比赛 + ML 概率 + AI 分析 + 赔率变动
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/api/upcoming-matches', methods=['GET'])
def get_upcoming_matches():
    """
    获取即将开赛的比赛列表（已有 ML 预测概率）。
    支持按联赛过滤、分页。
    Query params:
      league   - 联赛名称（如 英超）
      days     - 未来N天（默认14）
      page     - 页码（默认1）
      per_page - 每页条数（默认20，最大50）
    """
    try:
        league   = request.args.get('league')
        days     = min(int(request.args.get('days', 14)), 30)
        page     = max(int(request.args.get('page', 1)), 1)
        per_page = min(int(request.args.get('per_page', 20)), 50)
        offset   = (page - 1) * per_page

        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库未配置'}), 500

        with prediction_db.get_db_connection() as conn:
            cur = conn.cursor()

            # 构建查询
            base_cond = "WHERE status IN ('SCHEDULED','TIMED') AND match_time > NOW() AND match_time < NOW() + INTERVAL '%s days'"
            params = [days]
            if league:
                base_cond += " AND league_name = %s"
                params.append(league)

            # 总数
            cur.execute(f"SELECT COUNT(*) FROM upcoming_fixtures {base_cond}", params)
            total = cur.fetchone()[0]

            # 数据
            params_data = params + [per_page, offset]
            cur.execute(f"""
                SELECT uf.fixture_id, uf.league_code, uf.league_name,
                       uf.home_team, uf.away_team, uf.match_time, uf.matchday,
                       uf.home_odds, uf.draw_odds, uf.away_odds,
                       uf.ml_home_prob, uf.ml_draw_prob, uf.ml_away_prob,
                       uf.ml_recommendation,
                       mo.open_home_odds, mo.open_draw_odds, mo.open_away_odds
                FROM upcoming_fixtures uf
                LEFT JOIN match_odds mo ON (
                    mo.match_id = uf.fixture_id
                    OR (mo.home_team = uf.home_team AND mo.away_team = uf.away_team
                        AND mo.match_date = uf.match_time::date)
                )
                {base_cond}
                ORDER BY uf.match_time ASC
                LIMIT %s OFFSET %s
            """, params_data)

            rows = cur.fetchall()

        matches = []
        for r in rows:
            (fix_id, lg_code, lg_name, ht, at, mt, matchday,
             h_odds, d_odds, a_odds,
             ml_h, ml_d, ml_a, ml_rec,
             open_h, open_d, open_a) = r

            # 赔率变动计算
            odds_movement = {}
            if h_odds and open_h and float(open_h) > 0:
                odds_movement = {
                    'home_change':  round(float(h_odds) - float(open_h), 3),
                    'draw_change':  round(float(d_odds or 0) - float(open_d or 0), 3) if d_odds and open_d else 0,
                    'away_change':  round(float(a_odds or 0) - float(open_a or 0), 3) if a_odds and open_a else 0,
                    'signal':       _interpret_odds_signal(float(open_h), float(h_odds))
                }

            match_info = {
                'fixture_id':   fix_id,
                'league':       lg_name,
                'league_code':  lg_code,
                'home_team':    ht,
                'away_team':    at,
                'match_time':   mt.isoformat() if mt else None,
                'matchday':     matchday,
                'current_odds': {
                    'home': float(h_odds) if h_odds else None,
                    'draw': float(d_odds) if d_odds else None,
                    'away': float(a_odds) if a_odds else None,
                },
                'open_odds': {
                    'home': float(open_h) if open_h else None,
                    'draw': float(open_d) if open_d else None,
                    'away': float(open_a) if open_a else None,
                },
                'odds_movement': odds_movement,
                'ml_prediction': {
                    'home_prob':   float(ml_h) if ml_h else None,
                    'draw_prob':   float(ml_d) if ml_d else None,
                    'away_prob':   float(ml_a) if ml_a else None,
                    'recommendation': ml_rec,
                } if ml_h else None,
            }
            matches.append(match_info)

        return jsonify({
            'success':   True,
            'total':     total,
            'page':      page,
            'per_page':  per_page,
            'matches':   matches,
        })

    except Exception as e:
        app.logger.error(f"获取未开赛比赛失败: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


def _interpret_odds_signal(open_odds: float, current_odds: float) -> str:
    """解读赔率变动信号"""
    if open_odds <= 0:
        return 'neutral'
    change_pct = (current_odds - open_odds) / open_odds
    if change_pct <= -0.10:
        return 'strong_down'   # 赔率大幅下降，大量资金流入，看好该结果
    elif change_pct <= -0.04:
        return 'down'
    elif change_pct >= 0.10:
        return 'strong_up'     # 赔率大幅上升，资金流出，市场不看好
    elif change_pct >= 0.04:
        return 'up'
    return 'stable'


@app.route('/api/smart-predict', methods=['POST'])
def smart_predict():
    """
    单场智能预测：ML 概率 + 赔率变动分析 + (可选) Gemini AI 深度分析。
    Body JSON:
      fixture_id  - 赛程ID (必须)
      with_ai     - 是否调用 Gemini 深度分析（默认 false，消耗积分）
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'message': '请先登录'}), 401

        data       = request.get_json() or {}
        fixture_id = data.get('fixture_id', '').strip()
        with_ai    = bool(data.get('with_ai', False))

        if not fixture_id:
            return jsonify({'success': False, 'message': 'fixture_id 不能为空'}), 400

        if not prediction_db:
            return jsonify({'success': False, 'message': '数据库未配置'}), 500

        # 1. 从数据库拿比赛信息
        with prediction_db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fixture_id, league_code, league_name,
                       home_team, away_team, match_time,
                       home_odds, draw_odds, away_odds,
                       ml_home_prob, ml_draw_prob, ml_away_prob, ml_recommendation
                FROM upcoming_fixtures
                WHERE fixture_id = %s
            """, (fixture_id,))
            row = cur.fetchone()

        if not row:
            return jsonify({'success': False, 'message': '比赛不存在或已开赛'}), 404

        (fix_id, lg_code, lg_name, ht, at, mt,
         h_odds, d_odds, a_odds,
         ml_h, ml_d, ml_a, ml_rec) = row

        # 2. 检查积分（AI 分析消耗2积分，ML only 消耗1积分）
        cost = 2 if with_ai else 1
        credits = prediction_db.get_user_credits(current_user['id'])
        if credits < cost:
            return jsonify({
                'success':  False,
                'message':  f'积分不足（需要{cost}积分，当前{credits}积分），请签到获取积分'
            }), 403

        # 3. 组装响应
        result = {
            'fixture_id':  fix_id,
            'league':      lg_name,
            'home_team':   ht,
            'away_team':   at,
            'match_time':  mt.isoformat() if mt else None,
            'current_odds': {
                'home': float(h_odds) if h_odds else None,
                'draw': float(d_odds) if d_odds else None,
                'away': float(a_odds) if a_odds else None,
            },
            'ml_prediction': {
                'home_prob':       float(ml_h) if ml_h else None,
                'draw_prob':       float(ml_d) if ml_d else None,
                'away_prob':       float(ml_a) if ml_a else None,
                'recommendation':  ml_rec,
            } if ml_h else None,
        }

        # 4. 赔率变动（从 match_odds 取开盘赔率）
        try:
            with prediction_db.get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT home_odds, draw_odds, away_odds,
                           open_home_odds, open_draw_odds, open_away_odds
                    FROM match_odds
                    WHERE match_id = %s
                    ORDER BY updated_at DESC LIMIT 1
                """, (fix_id,))
                odds_row = cur.fetchone()

            if odds_row:
                cur_h, cur_d, cur_a, op_h, op_d, op_a = odds_row
                odds_mv = {}
                if cur_h and op_h:
                    odds_mv = {
                        'home_change': round(float(cur_h) - float(op_h), 3),
                        'draw_change': round(float(cur_d or 0) - float(op_d or 0), 3),
                        'away_change': round(float(cur_a or 0) - float(op_a or 0), 3),
                        'signal':      _interpret_odds_signal(float(op_h), float(cur_h)),
                    }
                result['odds_movement'] = odds_mv
        except Exception:
            pass

        # 5. Gemini AI 深度分析（可选）
        ai_analysis = None
        if with_ai:
            can_predict = prediction_db.can_user_predict(
                current_user['id'],
                current_user['user_type'],
                current_user['daily_predictions_used']
            )
            if not can_predict:
                return jsonify({'success': False, 'message': '今日预测次数已用完'}), 403

            gemini_key = os.environ.get('GEMINI_API_KEY')
            if gemini_key and AIFootballPredictor:
                try:
                    predictor = AIFootballPredictor(
                        api_key    = gemini_key,
                        model_name = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash-exp')
                    )
                    match_input = {
                        'match_id':    fix_id,
                        'home_team':   ht,
                        'away_team':   at,
                        'league_name': lg_name,
                        'home_odds':   float(h_odds) if h_odds else 2.0,
                        'draw_odds':   float(d_odds) if d_odds else 3.2,
                        'away_odds':   float(a_odds) if a_odds else 2.8,
                    }
                    analyses = predictor.analyze_matches([match_input], use_db=True)
                    if analyses:
                        ai_analysis = analyses[0].ai_analysis
                        result['ai_recommendation'] = analyses[0].recommendation
                except Exception as e:
                    app.logger.error(f"AI 分析失败: {e}")

            result['ai_analysis'] = ai_analysis
            prediction_db.increment_user_predictions(current_user['id'])

        # 6. 扣积分
        try:
            with prediction_db.get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE users SET credits = credits - %s WHERE id = %s",
                    (cost, current_user['id'])
                )
                conn.commit()
        except Exception as e:
            app.logger.warning(f"扣积分失败: {e}")

        result['credits_used']      = cost
        result['credits_remaining'] = max(credits - cost, 0)
        result['success'] = True
        return jsonify(result)

    except Exception as e:
        app.logger.error(f"智能预测失败: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/sync-upcoming', methods=['POST'])
def trigger_sync_upcoming():
    """管理员接口：手动触发赛程同步（仅 admin 可用）"""
    try:
        current_user = get_current_user()
        if not current_user or current_user.get('user_type') != 'admin':
            return jsonify({'success': False, 'message': '仅管理员可操作'}), 403

        import subprocess
        result = subprocess.run(
            ['python', 'scripts/sync_upcoming.py', '--days', '14'],
            capture_output=True, text=True, timeout=120,
            env={**os.environ,
                 'DB_HOST': os.environ.get('DB_HOST', ''),
                 'DB_PORT': os.environ.get('DB_PORT', '5432'),
                 'DB_NAME': os.environ.get('DB_NAME', ''),
                 'DB_USER': os.environ.get('DB_USER', ''),
                 'DB_PASS': os.environ.get('DB_PASS', ''),
            }
        )
        return jsonify({
            'success':   result.returncode == 0,
            'stdout':    result.stdout[-2000:],
            'stderr':    result.stderr[-500:] if result.returncode != 0 else '',
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/leagues', methods=['GET'])
def get_available_leagues():
    """获取数据库中有未开赛比赛的联赛列表"""
    try:
        if not prediction_db:
            return jsonify({'success': False}), 500
        with prediction_db.get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT league_name, league_code, COUNT(*) as cnt
                FROM upcoming_fixtures
                WHERE status IN ('SCHEDULED','TIMED') AND match_time > NOW()
                GROUP BY league_name, league_code
                ORDER BY cnt DESC
            """)
            rows = cur.fetchall()
        leagues = [{'name': r[0], 'code': r[1], 'upcoming_matches': r[2]} for r in rows]
        return jsonify({'success': True, 'leagues': leagues})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ── 应用入口 ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # 启动时确保积分字段存在
    if prediction_db:
        try:
            prediction_db.ensure_credits_columns()
        except Exception as e:
            app.logger.warning(f"积分字段初始化跳过: {e}")

    port = int(os.environ.get('PORT', 8000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.logger.info(f"🚀 MatchPredict 启动 → http://0.0.0.0:{port}")
    # use_reloader=False: 避免 debug 模式下 APScheduler 启动两次
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=False)