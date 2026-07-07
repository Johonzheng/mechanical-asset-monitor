import yfinance as yf
import pandas as pd
import requests
import os
import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= 核心系统配置 =================
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASS = os.environ.get('EMAIL_PASS')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, 'portfolio.csv')
REPORT_DIR = os.path.join(BASE_DIR, 'reports')

# 定义市场分类排序逻辑与对应的 Emoji 图标
MARKET_CONFIG = {
    'A股': '🇨🇳', 
    '港股': '🇭🇰', 
    '美股': '🇺🇸', 
    '其他海外市场': '🌍', 
    '国内外债券': '📜', 
    '大宗商品及贵金属': '🛢️', 
    '加密货币': '🪙'
}
# ================================================

def load_portfolio():
    if not os.path.exists(CSV_FILE): 
        print(f"⚠️ 未找到 {CSV_FILE} 文件，系统终止。")
        return []
    
    try:
        try:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='utf-8', dtype=str)
        except UnicodeDecodeError:
            df = pd.read_csv(CSV_FILE, skipinitialspace=True, encoding='gbk', dtype=str)
            
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        if 'name' not in df.columns: df['name'] = df['ticker']
        else: df['name'] = df['name'].fillna(df['ticker'])
            
        if 'pool' not in df.columns: df['pool'] = 'core'
        else: df['pool'] = df['pool'].fillna('core').str.strip().str.lower()
            
        if 'market' not in df.columns: df['market'] = 'A股'
        else: df['market'] = df['market'].fillna('未知市场').str.strip()
            
        return df[['ticker', 'name', 'type', 'pool', 'market']].to_dict('records')
    except Exception as e:
        print(f"清单读取与解析失败: {e}")
        return []

def align_to_last_friday(df):
    if df is None or df.empty: return None
    try:
        df.columns = [str(c).strip().lower() for c in df.columns]
        if df.index.tz is not None: df.index = df.index.tz_localize(None)
        today = pd.Timestamp.today().normalize()
        days_since_friday = (today.dayofweek - 4) % 7
        last_friday = today - pd.Timedelta(days=days_since_friday)
        
        df_filtered = df[df.index <= (last_friday + pd.Timedelta(hours=23, minutes=59))]
        if df_filtered.empty: return None
            
        weekly_df = df_filtered.resample('W-FRI').agg({
            'close': 'last', 'high': 'max', 'low': 'min'
        }).dropna()
        if len(weekly_df) < 2: return None
        return weekly_df
    except Exception: return None

def analyze_asset(weekly_df, item):
    weekly_df.columns = [str(c).lower() for c in weekly_df.columns]
    prev_week = weekly_df.iloc[-2]
    last_week = weekly_df.iloc[-1]
    
    current_price = last_week['close']
    prev_price = prev_week['close']
    pct_change = ((current_price - prev_price) / prev_price) * 100
    
    extremum_signal, ma_trend = "", "未知"
    if len(weekly_df) >= 20:
        weekly_df['ma5'] = weekly_df['close'].rolling(window=5).mean()
        weekly_df['ma20'] = weekly_df['close'].rolling(window=20).mean()
        lw_ma5, lw_ma20 = weekly_df.iloc[-1]['ma5'], weekly_df.iloc[-1]['ma20']
        
        if pd.notna(lw_ma5) and pd.notna(lw_ma20):
            if lw_ma5 >= lw_ma20: ma_trend = "多头"
            else: ma_trend = "空头"
            
        if len(weekly_df) >= 52:
            past_52_weeks = weekly_df.iloc[-53:-1]
            if last_week['close'] >= past_52_weeks['high'].max(): extremum_signal = "新高"
            elif last_week['close'] <= past_52_weeks['low'].min(): extremum_signal = "新低"
                
    return {
        "ticker": item['ticker'], 
        "name": item['name'], 
        "type": item['type'], 
        "pool": item['pool'], 
        "market": item['market'],
        "current_price": current_price,
        "pct_change": pct_change,
        "extremum_signal": extremum_signal,
        "ma_trend": ma_trend
    }

def fetch_yf_data(item):
    ticker = item['ticker']
    clean_code = str(ticker).split('.')[0]
    
    if '.SS' in ticker or '.SZ' in ticker:
        sec_id = f"1.{clean_code}" if ticker.endswith('.SS') or clean_code.startswith(('5', '6')) else f"0.{clean_code}"
        em_url = f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={sec_id}&klt=101&fqt=1&end=20500101&lmt=100&fields1=f1,f2,f3&fields2=f51,f53,f54,f55"
        try:
            res = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if res.status_code == 200:
                json_data = res.json()
                if json_data.get('data') and json_data['data'].get('klines'):
                    klines = json_data['data']['klines']
                    if len(klines) >= 5:
                        cleaned_rows = []
                        for k in klines:
                            parts = k.split(',')
                            cleaned_rows.append({'date': parts[0], 'close': float(parts[1]), 'high': float(parts[2]), 'low': float(parts[3])})
                        df = pd.DataFrame(cleaned_rows)
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)
                        aligned_df = align_to_last_friday(df)
                        if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass

        prefix = 'sh' if '.SS' in ticker else 'sz'
        tc = f"{prefix}{clean_code}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,100,qfq"
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if res.status_code == 200:
                json_data = res.json()
                if 'data' in json_data and tc in json_data['data']:
                    stock_data = json_data['data'][tc]
                    k_data = stock_data.get('qfqday', stock_data.get('day', []))
                    if k_data and len(k_data) >= 5:
                        cleaned_rows = []
                        for row in k_data:
                            if len(row) >= 6: cleaned_rows.append({'date': row[0], 'close': float(row[2]), 'high': float(row[3]), 'low': float(row[4])})
                        df = pd.DataFrame(cleaned_rows)
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)
                        aligned_df = align_to_last_friday(df)
                        if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass
        return None

    else:
        try:
            yf_ticker = ticker
            if '.HK' in ticker and len(clean_code) == 5 and clean_code.startswith('0'): yf_ticker = f"{clean_code[1:]}.HK"
            data = yf.Ticker(yf_ticker).history(period="1y", interval="1d", auto_adjust=True)
            if len(data) > 0:
                aligned_df = align_to_last_friday(data)
                if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass
            
        try:
            target_codes = []
            if '.HK' in ticker: target_codes = ['hk' + clean_code]
            else: target_codes = ['us' + str(ticker), 'us' + str(ticker).replace('-', '.'), 'us' + str(ticker).replace('-', '_'), 'us' + str(ticker).replace('-', '')]
            for tc in target_codes:
                url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,100,qfq"
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
                if res.status_code == 200:
                    json_data = res.json()
                    if 'data' in json_data and tc in json_data['data']:
                        stock_data = json_data['data'][tc]
                        k_data = stock_data.get('qfqday', stock_data.get('day', []))
                        if k_data and len(k_data) >= 5:
                            cleaned_rows = []
                            for row in k_data:
                                if len(row) >= 6: cleaned_rows.append({'date': row[0], 'close': float(row[2]), 'high': float(row[3]), 'low': float(row[4])})
                            df = pd.DataFrame(cleaned_rows)
                            df['date'] = pd.to_datetime(df['date'])
                            df.set_index('date', inplace=True)
                            aligned_df = align_to_last_friday(df)
                            if aligned_df is not None: return analyze_asset(aligned_df, item)
        except: pass
        return None

def fetch_fund_data(item):
    clean_ticker = str(item['ticker']).replace('jj', '').strip()
    em_url = f"http://fund.eastmoney.com/pingzhongdata/{clean_ticker}.js"
    try:
        res = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            content = res.text
            start_idx = content.find('Data_netWorthTrend = ') + len('Data_netWorthTrend = ')
            end_idx = content.find(';', start_idx)
            import json
            data = json.loads(content[start_idx:end_idx])
            cleaned_rows = []
            for entry in data:
                import time
                date_str = time.strftime('%Y-%m-%d', time.localtime(entry['x']/1000))
                cleaned_rows.append({'date': date_str, 'close': float(entry['y']), 'high': float(entry['y']), 'low': float(entry['y'])})
            
            df = pd.DataFrame(cleaned_rows)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            aligned_df = align_to_last_friday(df)
            if aligned_df is not None: return analyze_asset(aligned_df, item)
    except: pass
    return None

def fetch_crypto_data(item):
    ticker = item['ticker']
    try:
        yf_symbol = str(ticker).upper().replace('/USDT', '-USD').replace('/USDC', '-USD')
        data = yf.Ticker(yf_symbol).history(period="1y", interval="1d", auto_adjust=True)
        if len(data) > 0:
            aligned_df = align_to_last_friday(data)
            if aligned_df is not None: return analyze_asset(aligned_df, item)
    except: pass
    return None

# ================= 🚀 HTML UI 渲染引擎 =================

def build_html_table(assets):
    """构建单类别下的资产表格"""
    if not assets: return ""
    html = '<table style="width:100%; border-collapse: collapse; font-family: -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif;">'
    html += '<tr style="color:#888; font-size:12px; border-bottom:1px solid #eaeaea;">'
    html += '<th style="text-align:left; padding:8px 4px; font-weight:normal;">名称/代码</th>'
    html += '<th style="text-align:right; padding:8px 4px; font-weight:normal;">最新</th>'
    html += '<th style="text-align:right; padding:8px 4px; font-weight:normal;">涨幅</th>'
    html += '<th style="text-align:right; padding:8px 4px; font-weight:normal;">趋势</th></tr>'
    
    for r in assets:
        pct = r['pct_change']
        if pct > 0: color, sign = "#F9293E", "+"
        elif pct < 0: color, sign = "#00AA3B", ""
        else: color, sign = "#999999", ""
            
        if r['type'] == 'crypto': price_dec = 2
        elif r['type'] == 'jj': price_dec = 4
        else: price_dec = 3
            
        price_str = f"{r['current_price']:.{price_dec}f}"
        pct_str = f"{sign}{pct:.2f}%"
        
        # 极值标签渲染
        extreme_tag = ""
        if r.get('extremum_signal') == '新高':
            extreme_tag = ' <span style="background-color:#F9293E; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; margin-left:4px; vertical-align:text-bottom;">新高</span>'
        elif r.get('extremum_signal') == '新低':
            extreme_tag = ' <span style="background-color:#00AA3B; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; margin-left:4px; vertical-align:text-bottom;">新低</span>'

        # 多空趋势渲染
        trend_str = r.get('ma_trend', '未知')
        if trend_str == "多头": trend_color = "#F9293E"
        elif trend_str == "空头": trend_color = "#00AA3B"
        else: trend_color = "#999999"
        
        # padding 调整为 10px 4px，名称使用 flex 布局以对齐标签
        html += '<tr style="border-bottom:1px solid #f5f5f5;">'
        html += f'<td style="padding:10px 4px;"><div style="font-size:15px; color:#333; font-weight:bold; margin-bottom:2px; display:flex; align-items:center;"><span>{r["name"]}</span>{extreme_tag}</div><div style="font-size:11px; color:#999; font-family:Consolas, monospace;">{r["ticker"]}</div></td>'
        html += f'<td style="text-align:right; padding:10px 4px; color:{color}; font-size:16px; font-weight:600;">{price_str}</td>'
        html += f'<td style="text-align:right; padding:10px 4px; color:{color}; font-size:15px; font-weight:600;">{pct_str}</td>'
        html += f'<td style="text-align:right; padding:10px 4px; color:{trend_color}; font-size:14px; font-weight:bold;">{trend_str}</td>'
        html += '</tr>'
        
    html += '</table>'
    return html

def build_pool_html(pool_title, pool_assets):
    """构建核心/备选大模块（包含内部标签分割）"""
    if not pool_assets: return ""
    
    # 模块大标题
    html = f'<div style="font-size:18px; font-weight:bold; color:#111; margin: 30px 0 15px 0; padding-left: 10px; border-left: 4px solid #337ab7;">{pool_title}</div>'
    
    # 按照 MARKET_CONFIG 定义的市场顺序进行渲染
    for market, emoji in MARKET_CONFIG.items():
        market_assets = [a for a in pool_assets if a.get('market') == market]
        if market_assets:
            # 仿截图的灰色背景分割标签带
            html += f'<div style="background-color: #f4f5f7; color: #444; font-size: 14px; font-weight: bold; padding: 8px 12px; margin-top: 15px; border-radius: 4px; display: flex; align-items: center;">{emoji} &nbsp;{market}</div>'
            html += build_html_table(market_assets)
            
    # 处理未明确在配置列表中的其他市场资产
    other_assets = [a for a in pool_assets if a.get('market') not in MARKET_CONFIG.keys()]
    if other_assets:
        html += f'<div style="background-color: #f4f5f7; color: #444; font-size: 14px; font-weight: bold; padding: 8px 12px; margin-top: 15px; border-radius: 4px; display: flex; align-items: center;">🏷️ &nbsp;其他分类</div>'
        html += build_html_table(other_assets)
        
    return html

def send_email_report(all_reports, failed_list):
    if not all_reports:
        print("没有成功获取的数据，跳过发送邮件。")
        return

    # 全局降序排序
    all_reports.sort(key=lambda x: x['pct_change'], reverse=True)
    
    # 提取三大池
    core_pool = [r for r in all_reports if 'core' in str(r.get('pool', '')).lower()]
    watch_pool = [r for r in all_reports if 'core' not in str(r.get('pool', '')).lower() and 'watch' in str(r.get('pool', '')).lower()]
    
    # 构建 HTML 骨架
    html_content = f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body style="background-color: #f0f2f5; padding: 15px 5px; font-family: sans-serif; margin:0;">'
    html_content += f'<div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 20px 15px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);">'
    
    # 顶部标题
    html_content += f'<h2 style="text-align:center; color:#2c3e50; margin-top:5px; margin-bottom: 10px; font-size:22px;">📊 资产大盘全景看板</h2>'
    html_content += f'<div style="text-align:center; color:#888; font-size:12px; margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 15px;">生成时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}</div>'
    
    # 渲染核心模块与备选模块
    if core_pool: html_content += build_pool_html("💼 核心持仓", core_pool)
    if watch_pool: html_content += build_pool_html("👀 备选观察", watch_pool)
        
    # 渲染异常信息大模块 (直接罗列)
    if failed_list:
        html_content += f'<div style="font-size:18px; font-weight:bold; color:#111; margin: 30px 0 15px 0; padding-left: 10px; border-left: 4px solid #e6a23c;">⚠️ 异常信息</div>'
        html_content += f'<div style="background-color: #fcf8e3; color: #e6a23c; font-size: 13px; padding: 12px; border-radius: 6px; border: 1px solid #faebcc; line-height: 1.6;">'
        html_content += f'<b>以下标的未能获取最新数据（请检查网络或停牌状态）：</b><br>'
        for f_item in failed_list:
            html_content += f'<div style="margin-top: 4px;">• {f_item["name"]} <span style="color:#aaa; font-family:monospace;">({f_item["ticker"]})</span></div>'
        html_content += f'</div>'
        
    html_content += '</div></body></html>'

    # 本地归档保存
    try:
        if not os.path.exists(REPORT_DIR): os.makedirs(REPORT_DIR)
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        file_path = os.path.join(REPORT_DIR, f"{today_str}_weekly_report.html")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"✅ HTML 报告已成功本地归档，路径: {file_path}")
    except Exception as e:
        print(f"⚠️ 报告归档失败: {e}")

    # 发送邮件引擎
    if not EMAIL_USER or not EMAIL_PASS:
        print("未检测到 EMAIL_USER 或 EMAIL_PASS 环境变量，跳过邮件推送。")
        return
        
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_USER 
        msg['Subject'] = f"📈 资产大盘全景看板 ({datetime.datetime.now().strftime('%m-%d')})"
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        domain = EMAIL_USER.split('@')[-1]
        smtp_server = f"smtp.{domain}"
        
        server = smtplib.SMTP_SSL(smtp_server, 465)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_USER, msg.as_string())
        server.quit()
        print(f"✅ 邮件推送成功！已通过 {smtp_server} 发送。")
    except Exception as e:
        print(f"❌ 邮件推送失败: {e}")

if __name__ == "__main__":
    start_time = datetime.datetime.now()
    print(f"[{start_time}] 引擎点火，执行标签分割与多空阵营渲染...")
    
    assets_清单 = load_portfolio()
    all_reports = []
    failed_assets = [] 
    
    for item in assets_清单:
        res = None
        if item['type'] == 'yf': res = fetch_yf_data(item)
        elif item['type'] == 'crypto': res = fetch_crypto_data(item)
        elif item['type'] == 'jj': res = fetch_fund_data(item)
        
        if res: all_reports.append(res)
        else: failed_assets.append(item)
            
    send_email_report(all_reports, failed_assets)
    print(f"扫描完毕，总耗时: {(datetime.datetime.now() - start_time).seconds} 秒")
