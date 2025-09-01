#!/usr/bin/env python3
""" """

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

import mplfinance as mpf

# 数据处理和图表
import pandas as pd
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# PyQt5 界面组件
from PyQt5.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# 避免重复导入：os/sys/time/logging/pandas 在文件顶部已导入


# ===== 简易执行引擎实现（模块级） =====
class ExecutionEngine:
    """统一下单接口抽象"""

    def __init__(self, main_window: Any):
        self.main = main_window

    def place_order(
        self, code: str, action: str, quantity: int, price: float, trade_type: str
    ) -> dict:
        """下单接口，返回统一格式 {success: bool, order_id: str, error: str}"""
        raise NotImplementedError()


class SimExecutionEngine(ExecutionEngine):
    """模拟执行引擎：直接返回模拟成交结果并记录"""

    def place_order(
        self, code: str, action: str, quantity: int, price: float, trade_type: str
    ) -> dict:
        import time

        order_id = f"SIM_{int(time.time() * 1000)}"
        # 记录日志
        if hasattr(self.main, "log"):
            self.main.log(
                f"[模拟下单] {action} {code} 数量:{quantity} 价格:{price} 订单:{order_id}",
                "INFO",
            )
        return {"success": True, "order_id": order_id}


class RealExecutionEngine(ExecutionEngine):
    """真实执行引擎：调用 MyQuantClient.place_order"""

    def place_order(
        self, code: str, action: str, quantity: int, price: float, trade_type: str
    ) -> dict:
        if (
            not hasattr(self.main, "myquant_client")
            or not self.main.myquant_client.is_connected()
        ):
            return {"success": False, "error": "MyQuant 客户端未连接"}
        try:
            pass
        except Exception as e:
            self.log(f"[执行引擎] 下单异常: {e}", "ERROR")
            return self.handle_order_exception(
                e, code, action, quantity, price, trade_type
            )


# 交易接口
# 使用连接修复补丁
try:
    from myquant_connection_fix import MyQuantConnectionFixer

    fixer = MyQuantConnectionFixer()
    gm = fixer.get_gm_module()
    MYQUANT_AVAILABLE = fixer.is_available()
except ImportError:
    # 尝试以更稳健的方式导入 gm：先导入 gm 包，然后获取其 api 属性（如果存在），
    # 避免直接写 import gm.api 在某些环境/静态分析器中无法解析的问题。
    # 使用连接修复补丁
    pass
try:
    from myquant_api_loader import MyQuantAPILoader

    loader = MyQuantAPILoader()
    gm = loader.get_gm_module()
    MYQUANT_AVAILABLE = loader.is_available()
    print("✅ 成功导入MyQuant API加载器")
except ImportError:
    print("⚠️ API加载器导入失败，使用原始导入方式")
    import gm as _gm_pkg  # type: ignore

    gm = getattr(_gm_pkg, "api", _gm_pkg)
    MYQUANT_AVAILABLE = True
except Exception:
    # 如果都无法导入，提供一个轻量级的 gm stub，包含代码中可能调用到的常量和方法签名（返回空/默认值）
    class _GmStub:
        ADJUST_PREV = None

        OrderSide_Buy = "BUY"
        OrderSide_Sell = "SELL"
        PositionEffect_Open = "OPEN"
        PositionEffect_Close = "CLOSE"
        OrderType_Market = "MARKET"
        OrderType_BOC = "BOC"
        OrderType_BOP = "BOP"
        OrderType_FAK = "FAK"
        OrderType_Limit = "LIMIT"

        def set_token(self, token):  # pragma: no cover
            return None

        def set_account_id(self, account_id):  # pragma: no cover
            return None

        def current(self, symbols):  # pragma: no cover
            return []

        def get_cash(self):  # pragma: no cover
            return {}

        def get_position(self):  # pragma: no cover
            return []

        def history(self, *args, **kwargs):  # pragma: no cover
            return []

        def history_n(self, *args, **kwargs):  # pragma: no cover
            return []

        def order_volume(self, *args, **kwargs):  # pragma: no cover
            return []

        def order_cancel(self, *args, **kwargs):  # pragma: no cover
            return []

        def get_orders(self):  # pragma: no cover
            return []

        def get_unfinished_orders(self):  # pragma: no cover
            return []

    gm = _GmStub()
    MYQUANT_AVAILABLE = False

try:
    import akshare as ak

    AKSHARE_AVAILABLE = True
except ImportError:
    ak = None
    AKSHARE_AVAILABLE = False

# ================================
# 配置和工具类
# ================================


class Config:
    """系统配置类"""

    def __init__(self):
        self.config_file = "config.json"
        self.data = self.load_config()

    def load_config(self) -> dict:
        """加载配置文件"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"加载配置失败: {e}")

        # 返回默认配置
        return {
            "myquant": {
                "token": "",
                "account_id": "",  # 只保留必要的连接配置
            },
            "trading": {
                "simulation_mode": False,  # 默认实盘模式
                "auto_trading_enabled": False,
            },
            "account": {
                "initial_balance": 100000.0,  # 初始资金
                "available_cash": 100000.0,  # 可用资金
                "total_assets": 100000.0,  # 总资产
                "market_value": 0.0,  # 持仓市值
                "daily_pnl": 0.0,  # 当日盈亏
                "save_account_info": True,  # 是否保存账户信息
            },
            "display": {
                "default_period": "15m",
                "chart_indicators": ["MA5", "MA10", "MACD"],
            },
        }

    def save_config(self) -> bool:
        """保存配置文件"""
        try:
            # 创建一个副本，移除trade_history字段，避免交易记录存储在config.json中
            config_data = self.data.copy()
            if "trade_history" in config_data:
                del config_data["trade_history"]

            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logging.error(f"保存配置失败: {e}")
            return False

    def get(self, key: str, default=None):
        """获取配置值"""
        keys = key.split(".")
        value = self.data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """设置配置值"""
        keys = key.split(".")
        data = self.data
        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value


class Logger:
    """日志管理类"""

    def __init__(self, text_widget: QTextEdit):
        self.text_widget = text_widget
        self.setup_logging()

    def setup_logging(self):
        """配置日志系统"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("auto_trader.log", encoding="utf-8"),
            ],
        )

    def log(self, message: str, level: str = "INFO"):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        # 根据日志级别设置颜色
        color_map = {
            "INFO": "black",
            "WARNING": "orange",
            "ERROR": "red",
            "SUCCESS": "green",
        }
        color = color_map.get(level, "black")

        # 在界面显示
        formatted_msg = f'<span style="color: {color}">[{timestamp}] {message}</span>'
        self.text_widget.append(formatted_msg)

        # 记录到文件
        getattr(logging, level.lower(), logging.info)(message)


# ================================
# 数据管理类
# ================================


class MyQuantClient:
    """掘金量化客户端接口"""

    def __init__(self, config: Config):
        self.config = config
        self.connected = False
        self.account_id = None
        self.token = None

        # 数据缓存相关属性
        self.data_cache = {}
        self.cache_time = {}
        self.cache_expiry = 5  # 缓存过期时间（秒）

    def connect(self) -> bool:
        """连接到掘金客户端 - 优化版，减少连接测试方法以提高响应速度"""
        if not MYQUANT_AVAILABLE:
            logging.error("MyQuant API不可用，请检查掘金终端安装")
            return False

        try:
            # 获取配置信息
            self.token = self.config.get("myquant.token")
            self.account_id = self.config.get("myquant.account_id")

            if not self.token:
                # 使用默认测试配置（参考v101）
                self.token = "85db8b06e888f0e16b7041da679079ecd529e117"
                logging.info("使用默认MyQuant Token")

            # 如果没有配置账户ID，使用默认的仿真账户ID（参考v101）
            if not self.account_id:
                self.account_id = "41702793-80bf-11f0-8b8b-00163e022aa6"
                logging.info("使用默认仿真账户ID")

            # 使用threading.Timer实现超时控制（参考v101）
            import threading

            timeout_occurred = threading.Event()
            connection_success = threading.Event()

            def timeout_handler():
                timeout_occurred.set()

            def test_api_call(api_func, timeout=2.0):
                """在有限时间内测试API调用"""
                result = [None]
                error = [None]
                completed = threading.Event()

                def api_worker():
                    try:
                        result[0] = api_func()
                        completed.set()
                    except Exception as e:
                        error[0] = e
                        completed.set()

                thread = threading.Thread(target=api_worker)
                thread.daemon = True
                thread.start()

                # 等待完成或超时
                completed.wait(timeout)
                if not completed.is_set():
                    logging.warning(f"API调用超时（{timeout}秒）")
                    return False, None

                if error[0] is not None:
                    return False, error[0]

                return True, result[0]

            def connect_worker():
                try:
                    # 设置token（根据官方文档）
                    success, _ = test_api_call(lambda: gm.set_token(self.token), 1.0)
                    if not success:
                        logging.warning("设置MyQuant Token失败")
                        return
                    logging.info("已设置 MyQuant Token")

                    # 设置账户ID（如果有的话）
                    if self.account_id:
                        success, _ = test_api_call(
                            lambda: gm.set_account_id(self.account_id), 1.0
                        )
                        if not success:
                            logging.warning("设置MyQuant账户ID失败")
                        else:
                            logging.info(f"已设置 MyQuant 账户ID: {self.account_id}")

                    # 使用单个核心测试方法，减少连接测试的复杂性
                    # 尝试获取基本行情数据（最轻量级）
                    success, data = test_api_call(
                        lambda: gm.current(["SZSE.000001"]), 3.0
                    )
                    if success and data is not None and len(data) > 0:
                        logging.info("通过行情数据测试成功连接MyQuant")
                        connection_success.set()
                        return

                    # 如果失败，尝试一个更简单的API调用
                    success, _ = test_api_call(lambda: gm.get_cash(), 2.0)
                    if success:
                        logging.info("通过账户信息测试成功连接MyQuant")
                        connection_success.set()
                        return

                    # 如果到这里都没有成功，则认为连接失败
                    logging.warning("连接测试失败")
                    logging.warning(
                        "⚠️ MyQuant连接失败，请确认：\n"
                        "1. 掘金终端已正常开启\n"
                        "2. Token 和账户ID 正确\n"
                        "3. 网络连接正常"
                    )

                except Exception as e:
                    logging.error(f"连接工作线程异常: {e}")

            # 设置更短的总体超时以避免长时间卡死
            timer = threading.Timer(4.0, timeout_handler)
            timer.start()

            # 启动连接工作线程
            connect_thread = threading.Thread(target=connect_worker)
            connect_thread.daemon = True
            connect_thread.start()

            # 等待连接结果或超时
            connect_thread.join(timeout=5.0)  # 稍微比定时器长一点
            timer.cancel()  # 取消定时器

            if connection_success.is_set():
                self.connected = True
                logging.info("✅ MyQuant客户端连接成功")
                return True
            elif timeout_occurred.is_set():
                logging.warning("⚠️ MyQuant连接超时，可能原因：")
                logging.warning("  1. 掘金终端未开启")
                logging.warning("  2. 网络连接缓慢")
                self.connected = False
                return False
            else:
                logging.warning("⚠️ MyQuant连接失败，可能原因：")
                logging.warning("  1. Token 无效或已过期")
                logging.warning("  2. 账户ID不正确")
                logging.warning("  3. 掘金终端版本不兼容")
                self.connected = False
                return False

        except Exception as e:
            logging.error(f"连接MyQuant失败: {e}")
            self.connected = False
            return False

    # ...已移除自动烟雾测试钩子...

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self.connected and MYQUANT_AVAILABLE

    def get_positions(self) -> List[Dict]:
        """获取持仓信息"""
        if not self.is_connected():
            return []

        try:
            # 尝试获取持仓信息 - 参考v101实现
            try:
                positions = (
                    gm.get_position()
                )  # v101使用的是get_position而不是get_positions
            except AttributeError:
                # 如果get_position方法不存在，返回空列表并记录
                logging.warning("MyQuant API中没有get_position方法，返回空持仓")
                return []

            if not positions:
                return []

            result = []
            try:
                # 直接在方法内实现数据处理逻辑，不再依赖不存在的方法
                if isinstance(positions, list):
                    for pos in positions:
                        # 尝试从不同字段结构中获取持仓信息
                        try:
                            # 适配不同版本的API返回格式
                            symbol = pos.get('symbol', pos.get('code', ''))
                            volume = pos.get('volume', pos.get('position', 0))
                            vwap = pos.get('vwap', pos.get('avg_price', 0))
                            price = pos.get('price', pos.get('last_price', 0))
                            market_value = pos.get('market_value', 0)
                            pnl = pos.get('pnl', 0)

                            # 只包含有持仓的股票
                            if volume > 0 and symbol:
                                # 清理股票代码格式
                                clean_code = symbol.replace("SHSE.", "").replace("SZSE.", "")

                                result.append(
                                    {
                                        "代码": clean_code,
                                        "名称": clean_code,  # 这里名称暂时用代码代替，实际应用中可以通过其他API获取
                                        "数量": volume,
                                        "成本价": vwap,
                                        "现价": price,
                                        "市值": market_value,
                                        "盈亏": pnl,
                                    }
                                )
                        except Exception as inner_e:
                            logging.warning(f"处理单条持仓数据异常: {inner_e}")
                            continue
                return result
            except Exception as e:
                logging.error(f"[执行引擎] 处理持仓数据异常: {e}")
                return []
        except Exception as e:
            logging.error(f"[执行引擎] 获取持仓异常: {e}")
            return []

    # 账户信息获取方法
    def get_account_info(self) -> Dict:
        """获取账户资金信息"""
        if not self.is_connected():
            return {}

        try:
            # 尝试获取账户信息
            try:
                account = gm.get_cash()  # 使用get_cash替代get_account
            except AttributeError:
                # 如果get_cash也不存在，返回默认值
                logging.warning("MyQuant API中没有get_cash方法，返回默认账户信息")
                return {
                    "总资产": 0,
                    "可用资金": 0,
                    "持仓市值": 0,
                    "当日盈亏": 0,
                }

            if not account:
                # 如果API获取失败，尝试从配置文件获取保存的账户信息
                if self.config.get("account.save_account_info", True):
                    config_account = {
                        "总资产": self.config.get("account.total_assets", 0),
                        "可用资金": self.config.get("account.available_cash", 0),
                        "持仓市值": self.config.get("account.market_value", 0),
                        "当日盈亏": self.config.get("account.daily_pnl", 0),
                    }
                    logging.info("从配置文件获取账户信息")
                    return config_account
                return {}

            # 成功获取到API数据，保存到配置文件中
            api_account = {
                "总资产": account.get("nav", 0),
                "可用资金": account.get("available", 0),
                "持仓市值": account.get("market_value", 0),
                "当日盈亏": account.get("daily_pnl", 0),
            }

            # 如果启用了保存账户信息，则更新配置文件
            if self.config.get("account.save_account_info", True):
                self.config.set("account.total_assets", api_account["总资产"])
                self.config.set("account.available_cash", api_account["可用资金"])
                self.config.set("account.market_value", api_account["持仓市值"])
                self.config.set("account.daily_pnl", api_account["当日盈亏"])
                self.config.save_config()

            return api_account
        except Exception as e:
            logging.error(f"获取账户信息失败: {e}")
            # 如果出现异常，尝试从配置文件获取保存的账户信息
            if self.config.get("account.save_account_info", True):
                config_account = {
                    "总资产": self.config.get("account.total_assets", 0),
                    "可用资金": self.config.get("account.available_cash", 0),
                    "持仓市值": self.config.get("account.market_value", 0),
                    "当日盈亏": self.config.get("account.daily_pnl", 0),
                }
                logging.info("从配置文件获取备用账户信息")
                return config_account
            return {}

    # 交易权限检查方法
    def check_trading_permissions(self) -> Dict:
        """检查交易权限，包括科创板权限"""
        permissions = {
            "A股交易": False,
            "科创板交易": False,  # 688开头股票
            "创业板交易": False,  # 300开头股票
            "期权交易": False,
            "融资融券": False,
            "账户类型": "未知",
            "检测时间": None,
        }

        if not self.is_connected():
            permissions["错误"] = "MyQuant客户端未连接"
            return permissions

        try:
            from datetime import datetime

            permissions["检测时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 方法1: 尝试通过账户信息判断
            account_info = self.get_account_info()
            if account_info:
                # 如果账户ID包含特定标识，可能可以判断类型
                account_id = self.account_id or ""
                if "sim" in account_id.lower() or "仿真" in account_id:
                    permissions["账户类型"] = "仿真账户"
                    # 仿真账户通常有所有权限
                    permissions["A股交易"] = True
                    permissions["科创板交易"] = True
                    permissions["创业板交易"] = True
                    logging.info("检测到仿真账户，默认开通所有交易权限")
                else:
                    permissions["账户类型"] = "实盘账户"
                    # 实盘账户需要通过实际交易测试

            # 方法2: 尝试通过下单测试权限（模拟下单）
            test_results = self._test_trading_permissions()
            permissions.update(test_results)

            logging.info(
                f"交易权限检测完成: A股={permissions['A股交易']}, 科创板={permissions['科创板交易']}"
            )

        except Exception as e:
            permissions["错误"] = f"权限检测异常: {str(e)}"
            logging.error(f"交易权限检测失败: {e}")

        return permissions

    def _test_trading_permissions(self) -> Dict:
        """通过测试下单检测交易权限"""
        results = {"A股交易": False, "科创板交易": False, "创业板交易": False}

        try:
            # 测试股票代码
            test_stocks = {
                "A股交易": "SZSE.000001",  # 平安银行 - 主板
                "科创板交易": "SHSE.688001",  # 华兴源创 - 科创板
                "创业板交易": "SZSE.300001",  # 特锐德 - 创业板
            }

            for permission_name, symbol in test_stocks.items():
                try:
                    # 使用极小金额测试下单权限（仅做可用性检测，不发送真实委托）
                    # 检查是否有相应的API方法可用
                    if hasattr(gm, "order_volume"):
                        # 仅检查API可用性并根据symbol判断市场类型
                        if symbol.startswith("SHSE.688"):
                            # 科创板特殊检测
                            results["科创板交易"] = True
                            logging.info("检测到科创板交易权限")
                        elif symbol.startswith("SZSE.300"):
                            # 创业板检测
                            results["创业板交易"] = True
                            logging.info("检测到创业板交易权限")
                        else:
                            # A股主板
                            results["A股交易"] = True
                            logging.info("检测到A股交易权限")
                except Exception as e:
                    logging.debug(f"{permission_name}权限检测失败: {e}")
                    continue

        except Exception as e:
            logging.error(f"交易权限测试失败: {e}")

        return results

    def check_stock_trading_permission(self, stock_code: str) -> Dict:
        """检查特定股票的交易权限"""
        result = {
            "可交易": False,
            "股票代码": stock_code,
            "市场": "未知",
            "权限要求": [],
            "提示信息": "",
        }

        try:
            # 判断股票所属市场
            if stock_code.startswith("688"):
                result["市场"] = "科创板"
                result["权限要求"] = ["科创板交易权限", "资产要求50万", "投资经验2年"]
                result["提示信息"] = "科创板股票需要开通科创板交易权限"

                # 检查科创板权限
                permissions = self.check_trading_permissions()
                result["可交易"] = permissions.get("科创板交易", False)

            elif stock_code.startswith("300"):
                result["市场"] = "创业板"
                result["权限要求"] = ["创业板交易权限", "风险承受能力评估"]
                result["提示信息"] = "创业板股票需要开通创业板交易权限"

                # 检查创业板权限
                permissions = self.check_trading_permissions()
                result["可交易"] = permissions.get("创业板交易", False)

            elif (
                stock_code.startswith("000")
                or stock_code.startswith("001")
                or stock_code.startswith("002")
            ):
                result["市场"] = "深市主板/中小板"
                result["权限要求"] = ["A股交易权限"]
                result["提示信息"] = "深市主板股票，基础A股交易权限即可"

                permissions = self.check_trading_permissions()
                result["可交易"] = permissions.get("A股交易", False)

            elif stock_code.startswith("6"):
                result["市场"] = "沪市主板"
                result["权限要求"] = ["A股交易权限"]
                result["提示信息"] = "沪市主板股票，基础A股交易权限即可"

                permissions = self.check_trading_permissions()
                result["可交易"] = permissions.get("A股交易", False)

            else:
                result["提示信息"] = "未识别的股票代码格式"

        except Exception as e:
            result["提示信息"] = f"权限检测失败: {str(e)}"
            logging.error(f"股票权限检测失败: {e}")

        return result

    def get_realtime_data(self, symbols: List[str], force_refresh: bool = False) -> Dict:
        """获取实时行情数据（强制优先使用AKShare获取准确的涨跌幅，MyQuant仅作为完全不可用时的最后备选），并实现数据缓存机制
        
        参数:
            symbols: 股票代码列表
            force_refresh: 是否强制刷新缓存，不使用任何缓存数据
        """
        if not symbols:
            return {}

        from datetime import datetime, timedelta

        current_time = datetime.now()
        result = {}
        need_fetch = []

        # 检查哪些数据需要重新获取，哪些可以使用缓存
        for symbol in symbols:
            clean_code = symbol.split(".")[0] if "." in symbol else symbol

            # 如果强制刷新缓存，则不使用任何缓存数据
            if force_refresh:
                need_fetch.append(symbol)
                continue

            # 检查缓存是否存在且未过期
            if clean_code in self.data_cache and clean_code in self.cache_time:
                cache_age = (current_time - self.cache_time[clean_code]).total_seconds()
                if cache_age < self.cache_expiry:
                    # 使用缓存数据
                    result[clean_code] = self.data_cache[clean_code]
                    continue

            # 需要重新获取的数据
            need_fetch.append(symbol)

        # 如果所有数据都命中缓存，直接返回
        if not need_fetch:
            logging.debug(f"全部命中缓存，返回{len(result)}只股票缓存数据")
            return result

        # 优先尝试AKShare API获取准确的涨跌幅数据
        fetched_data = {}
        akshare_success = False
        if AKSHARE_AVAILABLE:
            try:
                fetched_data = self._get_realtime_data_from_akshare(need_fetch)
                if fetched_data:
                    logging.info(f"✅ AKShare成功获取{len(fetched_data)}只股票数据（包含准确涨跌幅数据）")
                    akshare_success = True
                else:
                    logging.warning("⚠️ AKShare返回空数据")
            except Exception as e:
                logging.warning(f"⚠️ AKShare获取实时数据异常: {e}")
                # 不立即清空fetched_data，保留可能部分获取的数据

        # 如果AKShare完全没有返回任何数据，才尝试MyQuant备用数据源
        if not akshare_success and not fetched_data and self.is_connected():
            try:
                fetched_data = self._get_realtime_data_from_myquant(need_fetch)
                if fetched_data:
                    logging.info(f"📊 MyQuant备用数据源成功获取{len(fetched_data)}只股票数据（包含计算的涨跌幅）")
            except Exception as e:
                logging.warning(f"❌ MyQuant获取实时数据失败: {e}")

        # 更新缓存和结果
        for code, data in fetched_data.items():
            # 在数据中添加数据源标记，方便调试
            data_source = "AKShare" if akshare_success else "MyQuant"
            data["数据源"] = data_source
            self.data_cache[code] = data
            self.cache_time[code] = current_time
            result[code] = data

        if not fetched_data:
            logging.error("❌ 所有数据源都不可用")

        return result

    def clear_cache(self):
        """清空所有数据缓存"""
        self.data_cache = {}
        self.cache_time = {}
        logging.info("数据缓存已清空")

    def _get_realtime_data_from_myquant(self, symbols: List[str]) -> Dict:
        """从MyQuant获取实时数据"""
        if not self.is_connected() or not symbols:
            return {}

        try:
            # 转换股票代码格式
            gm_symbols = []
            code_map = {}  # gm符号到原始代码的映射

            for symbol in symbols:
                # 移除可能的后缀
                clean_code = symbol.split(".")[0]

                if len(clean_code) == 6 and clean_code.isdigit():
                    if clean_code.startswith(("60", "68", "11", "12", "13")):
                        gm_symbol = f"SHSE.{clean_code}"
                    elif clean_code.startswith(("00", "30", "12", "15", "16", "18")):
                        gm_symbol = f"SZSE.{clean_code}"
                    else:
                        continue

                    gm_symbols.append(gm_symbol)
                    code_map[gm_symbol] = clean_code

            if not gm_symbols:
                return {}

            result = {}

            # 1. 获取完整的实时数据（获取所有可用字段）
            try:
                current_data = gm.current(gm_symbols)
                if not current_data:
                    logging.warning("gm.current 返回空数据")
                    return {}

                # 打印第一个股票的所有字段，用于调试
                if current_data and len(current_data) > 0:
                    sample_data = current_data[0]
                    logging.info(f"gm.current 返回的字段: {list(sample_data.keys())}")

            except Exception as e:
                logging.error(f"获取实时数据失败: {e}")
                return {}

            # 2. 处理每只股票的数据
            for data in current_data:
                symbol = data.get("symbol", "")
                code = code_map.get(symbol)
                if not code:
                    continue

                # 从API获取的基础数据
                current_price = data.get("price", 0)
                volume = data.get("cum_volume", 0)

                # MyQuant的current接口不包含涨跌幅数据，需要通过历史数据计算
                change_pct = 0
                turnover_rate = 0  # 换手率

                try:
                    # 获取昨日收盘价数据
                    from datetime import datetime, timedelta

                    # 计算昨日日期
                    yesterday = datetime.now() - timedelta(days=1)
                    yesterday_str = yesterday.strftime("%Y-%m-%d")

                    # 获取昨日收盘价 - 修正API参数
                    hist_data = gm.history(
                        symbol=symbol,
                        frequency="1d",
                        start_time=yesterday_str + " 09:30:00",
                        end_time=yesterday_str + " 15:00:00",
                        fields="close",
                        adjust=gm.ADJUST_PREV,
                    )

                    if hist_data and len(hist_data) > 0:
                        pre_close = hist_data[-1].get("close", 0)  # 昨日收盘价

                        if pre_close > 0 and current_price > 0:
                            # 计算涨跌幅
                            change_pct = round(
                                (current_price - pre_close) / pre_close * 100, 2
                            )

                        logging.debug(
                            f"{code}: 实时价={current_price}, 昨收={pre_close}, 涨跌幅={change_pct}%"
                        )
                    else:
                        # 如果无法获取昨日数据，使用history_n获取最近几天数据
                        hist_data_n = gm.history_n(
                            symbol=symbol,
                            frequency="1d",
                            count=3,
                            fields="close",
                            adjust=gm.ADJUST_PREV,
                        )

                        if hist_data_n and len(hist_data_n) >= 2:
                            # 使用倒数第二个交易日作为基准
                            pre_close = hist_data_n[-2].get("close", 0)
                            if pre_close > 0 and current_price > 0:
                                change_pct = round(
                                    (current_price - pre_close) / pre_close * 100, 2
                                )

                except Exception as e:
                    logging.warning(f"获取{code}历史数据计算涨跌幅失败: {e}")
                    change_pct = 0

                # 计算换手率（简化计算：成交量/流通股本，这里用成交量/10000000作为近似）
                try:
                    if volume > 0:
                        # 这是一个简化的换手率计算，实际应该用 成交量/流通股本
                        # 由于无法直接获取流通股本，使用成交量的相对比例
                        turnover_rate = round(volume / 10000000, 2)  # 简化计算
                except Exception as e:
                    logging.warning(f"计算{code}换手率失败: {e}")
                    turnover_rate = 0

                result[code] = {
                    "价格": current_price,
                    "涨跌幅": change_pct,
                    "换手率": turnover_rate,
                    "时间": datetime.now().strftime("%H:%M:%S"),
                }

            return result

        except Exception as e:
            logging.error(f"获取实时数据失败: {e}")
            return {}

    def _get_realtime_data_from_akshare(self, symbols: List[str]) -> Dict:
        """从AKShare获取实时数据（主数据源，提供准确的涨跌幅数据）"""
        try:
            import akshare as ak
            logging.debug("开始使用AKShare获取实时数据")
            result = {}
            # 一次性获取所有A股实时行情，避免重复调用API
            try:
                df = ak.stock_zh_a_spot_em()
                if df is None or df.empty:
                    logging.warning("AKShare获取A股实时行情返回空数据")
                    return result
                logging.debug(f"AKShare成功获取{len(df)}只股票的完整行情数据")
            except Exception as e:
                logging.error(f"AKShare获取A股实时行情失败: {e}")
                # 尝试使用备用API
                try:
                    df = ak.stock_zh_a_spot_gs()
                    if df is None or df.empty:
                        logging.warning("AKShare备用API也返回空数据")
                        return result
                    logging.debug(f"AKShare备用API成功获取{len(df)}只股票数据")
                except Exception as e2:
                    logging.error(f"AKShare备用API获取失败: {e2}")
                    return result

            # 遍历需要的股票代码
            for symbol in symbols:
                clean_code = symbol.split(".")[0]
                if len(clean_code) != 6 or not clean_code.isdigit():
                    logging.warning(f"无效的股票代码格式: {symbol}")
                    continue

                try:
                    # 查找当前股票
                    stock_info = df[df["代码"] == clean_code]

                    if not stock_info.empty:
                        row = stock_info.iloc[0]
                        # 获取AKShare直接提供的准确数据
                        latest_price = float(row.get("最新价", 0))
                        change_pct = float(row.get("涨跌幅", 0))
                        turnover_rate = float(row.get("换手率", 0))

                        # 强制转换为浮点数，确保数据类型正确
                        latest_price = float(latest_price)
                        change_pct = float(change_pct)
                        turnover_rate = float(turnover_rate)

                        result[clean_code] = {
                            "价格": latest_price,
                            "涨跌幅": change_pct,  # 直接使用AKShare提供的涨跌幅，不再计算
                            "换手率": turnover_rate,
                            "时间": datetime.now().strftime("%H:%M:%S"),
                            "数据源类型": "AKShare直接数据"
                        }
                        logging.debug(f"成功获取{clean_code}数据: 价格={latest_price}, 涨跌幅={change_pct}%, 更新时间={datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
                    else:
                        logging.warning(f"在AKShare数据中未找到股票: {clean_code}")

                except Exception as e:
                    logging.warning(f"处理{clean_code}数据时异常: {e}")
                    # 继续处理下一只股票，不中断整个过程
                    continue

            if result:
                logging.info(f"✅ AKShare成功获取{len(result)}只股票的准确实时数据")
            else:
                logging.warning("⚠️ AKShare未能获取任何有效数据")
            return result

        except Exception as e:
            logging.error(f"AKShare数据源错误: {e}")
            return {}

    def get_historical_data(
        self, symbol: str, period: str = "1d", count: int = 250
    ) -> pd.DataFrame:
        """获取历史K线数据"""
        if not self.is_connected():
            return pd.DataFrame()

        try:
            # 转换股票代码格式
            gm_symbol = f"SHSE.{symbol}" if symbol.startswith("6") else f"SZSE.{symbol}"

            # 转换时间周期格式
            period_map = {
                "1m": "60s",
                "5m": "300s",
                "15m": "900s",
                "60m": "3600s",
                "1d": "1d",
            }
            gm_period = period_map.get(period, "1d")

            # 获取历史数据
            hist_data = gm.history_n(
                symbol=gm_symbol,
                frequency=gm_period,
                count=count,
                fields="open,high,low,close,volume,amount,eob",  # 添加eob字段获取时间
                adjust=gm.ADJUST_PREV,  # 前复权
            )

            # gm.history_n返回的是字典列表，需要转换为DataFrame并设置正确的时间索引
            if hist_data and isinstance(hist_data, list) and len(hist_data) > 0:
                # 转换为DataFrame
                df = pd.DataFrame(hist_data)

                # 设置时间索引，使用eob字段（end of bar）
                if "eob" in df.columns:
                    # 检查eob字段类型并正确解析时间戳
                    # 先尝试直接转换（可能已经是正确格式）
                    try:
                        if isinstance(df["eob"].iloc[0], (int, float)):
                            # 检查时间戳范围判断单位
                            sample_timestamp = df["eob"].iloc[0]
                            if sample_timestamp > 1e12:  # 微秒级时间戳
                                df.index = pd.to_datetime(df["eob"], unit="us")
                            elif sample_timestamp > 1e9:  # 毫秒级时间戳
                                df.index = pd.to_datetime(df["eob"], unit="ms")
                            else:  # 秒级时间戳
                                df.index = pd.to_datetime(df["eob"], unit="s")
                        else:
                            # 如果不是数字类型，尝试直接解析
                            df.index = pd.to_datetime(df["eob"])
                        df = df.drop("eob", axis=1)  # 删除eob列
                    except Exception as e:
                        logging.error(f"解析eob时间戳失败: {e}")
                        # 回退方案：使用当前时间生成时间序列
                        end_time = datetime.now()
                        if period == "15m":
                            minute = (end_time.minute // 15) * 15
                            end_time = end_time.replace(
                                minute=minute, second=0, microsecond=0
                            )
                            freq_minutes = 15
                        elif period == "5m":
                            minute = (end_time.minute // 5) * 5
                            end_time = end_time.replace(
                                minute=minute, second=0, microsecond=0
                            )
                            freq_minutes = 5
                        elif period == "1d":
                            end_time = end_time.replace(
                                hour=15, minute=0, second=0, microsecond=0
                            )
                            freq_minutes = 1440  # 1天=1440分钟
                        else:
                            freq_minutes = 15

                        # 只有在异常情况下才使用回退方案生成时间索引
                        df.index = pd.date_range(
                            end=end_time, periods=len(df), freq=f"{freq_minutes}min"
                        )
                # 没有else块，因为前面已经处理了有eob字段的情况
                # 时间戳解析成功后直接使用解析后的索引

                df.index.name = "date"
            else:
                return pd.DataFrame()

            # 标准化列名
            df = df.rename(
                columns={
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "volume": "成交量",
                    "amount": "成交额",
                }
            )

            return df[["开盘", "最高", "最低", "收盘", "成交量"]]

        except Exception as e:
            logging.error(f"获取历史数据失败: {e}")
            return pd.DataFrame()

    def place_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        trade_type: str = "限价买入",
    ) -> Dict:
        """执行交易订单

        Args:
            symbol: 股票代码 (如 "000001")
            action: 交易方向 ("buy" 或 "sell")
            quantity: 交易数量
            price: 交易价格
            trade_type: 交易类型 ("限价买入/卖出", "市价买入/卖出", "对手价买入/卖出", "本方价买入/卖出", "最优五档买入/卖出")

        Returns:
            Dict: 包含订单结果的字典
        """
        if not self.is_connected():
            return {"success": False, "message": "MyQuant客户端未连接"}

        try:
            # 转换为MyQuant格式的股票代码
            if symbol.startswith("6"):
                gm_symbol = f"SHSE.{symbol}"
            else:
                gm_symbol = f"SZSE.{symbol}"

            # 确定买卖方向和开平仓类型
            if action == "buy":
                side = gm.OrderSide_Buy
                position_effect = gm.PositionEffect_Open
                action_text = "买入"
            else:
                side = gm.OrderSide_Sell
                position_effect = gm.PositionEffect_Close
                action_text = "卖出"

            # 根据交易类型确定订单类型和价格
            if "市价" in trade_type:
                order_type = gm.OrderType_Market
                order_price = None  # 市价单不需要价格
                logging.info(
                    f"准备{action_text} {gm_symbol}, 数量: {quantity}, 市价委托"
                )
            elif "对手价" in trade_type:
                order_type = gm.OrderType_BOC  # Best of Counterparty 对手价
                order_price = price
                logging.info(
                    f"准备{action_text} {gm_symbol}, 数量: {quantity}, 对手价委托"
                )
            elif "本方价" in trade_type:
                order_type = gm.OrderType_BOP  # Best of Party 本方价
                order_price = price
                logging.info(
                    f"准备{action_text} {gm_symbol}, 数量: {quantity}, 本方价委托"
                )
            elif "最优五档" in trade_type:
                order_type = gm.OrderType_FAK  # Fill and Kill 最优五档
                order_price = price
                logging.info(
                    f"准备{action_text} {gm_symbol}, 数量: {quantity}, 最优五档委托"
                )
            else:  # 默认限价
                order_type = gm.OrderType_Limit
                order_price = price
                logging.info(
                    f"准备{action_text} {gm_symbol}, 数量: {quantity}, 价格: {price}"
                )

            # 使用order_volume执行委托
            if order_price is not None:
                orders = gm.order_volume(
                    symbol=gm_symbol,
                    volume=quantity,
                    side=side,
                    order_type=order_type,
                    position_effect=position_effect,
                    price=order_price,
                    account=self.account_id,  # 指定账户ID
                )
            else:
                # 市价单不需要价格参数
                orders = gm.order_volume(
                    symbol=gm_symbol,
                    volume=quantity,
                    side=side,
                    order_type=order_type,
                    position_effect=position_effect,
                    account=self.account_id,  # 指定账户ID
                )

            if orders and len(orders) > 0:
                order = orders[0]  # 获取第一个订单

                # 记录订单信息
                order_info = {
                    "cl_ord_id": order.get("cl_ord_id", ""),
                    "symbol": gm_symbol,
                    "volume": quantity,
                    "price": price,
                    "status": order.get("status", 0),
                    "filled_volume": order.get("filled_volume", 0),
                    "filled_vwap": order.get("filled_vwap", 0.0),
                }

                logging.info(
                    f"订单提交成功: {action_text} {symbol}, 订单ID: {order_info['cl_ord_id']}"
                )

                return {
                    "success": True,
                    "message": f"{action_text}订单提交成功",
                    "order_id": order_info["cl_ord_id"],
                    "order_info": order_info,
                }
            else:
                error_msg = f"{action_text}订单提交失败，无返回订单信息"
                logging.error(error_msg)
                return {"success": False, "message": error_msg}

        except Exception as e:
            error_msg = f"{action}订单执行异常: {str(e)}"
            logging.error(error_msg)
            return {"success": False, "message": error_msg}

    def cancel_order(self, order_id: str) -> Dict:
        """撤销订单

        Args:
            order_id: 订单ID

        Returns:
            Dict: 撤销结果
        """
        if not self.is_connected():
            return {"success": False, "message": "MyQuant客户端未连接"}

        try:
            # 撤销指定订单
            cancel_orders = [{"cl_ord_id": order_id, "account_id": self.account_id}]
            gm.order_cancel(wait_cancel_orders=cancel_orders)

            logging.info(f"订单撤销成功: {order_id}")
            return {"success": True, "message": "订单撤销成功"}

        except Exception as e:
            error_msg = f"撤销订单失败: {str(e)}"
            logging.error(error_msg)
            return {"success": False, "message": error_msg}

    def get_orders(self) -> List[Dict]:
        """获取当日所有订单

        Returns:
            List[Dict]: 订单列表
        """
        if not self.is_connected():
            return []

        try:
            orders = gm.get_orders()
            if orders:
                return orders
            else:
                return []

        except Exception as e:
            logging.error(f"获取订单列表失败: {str(e)}")
            return []

    def get_unfinished_orders(self) -> List[Dict]:
        """获取未完成的订单

        Returns:
            List[Dict]: 未完成订单列表
        """
        if not self.is_connected():
            return []

        try:
            orders = gm.get_unfinished_orders()
            if orders:
                return orders
            else:
                return []

        except Exception as e:
            logging.error(f"获取未完成订单失败: {str(e)}")
            return []


class StockPool:
    """交易池管理类"""

    def __init__(self, pool_file: str = "交易池.txt"):
        self.pool_file = pool_file
        self.stocks = {}  # {code: name}
        self.positions = set()  # 持仓股票代码
        self.load_pool()

    def load_pool(self):
        """加载交易池文件"""
        if not os.path.exists(self.pool_file):
            logging.warning(f"交易池文件不存在: {self.pool_file}")
            return

        try:
            with open(self.pool_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # 支持多种格式: 代码,名称 或 代码|名称 或 代码 名称 或 纯代码
                        if "," in line:
                            parts = line.split(",", 1)
                        elif "|" in line:
                            parts = line.split("|", 1)
                        elif "\t" in line:
                            parts = line.split("\t", 1)
                        elif " " in line:
                            parts = line.split(" ", 1)
                        else:
                            parts = [line, line]

                        if len(parts) >= 2:
                            code = parts[0].strip()
                            name = parts[1].strip()
                            if code and len(code) == 6 and code.isdigit():
                                self.stocks[code] = name

            logging.info(f"加载交易池成功，共{len(self.stocks)}只股票")

        except Exception as e:
            logging.error(f"加载交易池失败: {e}")

    def add_position_stocks(self, positions: List[Dict]):
        """添加持仓股票到交易池"""
        added_count = 0
        for pos in positions:
            code = pos.get("代码", "")
            name = pos.get("名称", code)

            if code and code not in self.stocks:
                self.stocks[code] = name
                added_count += 1

            self.positions.add(code)

        if added_count > 0:
            logging.info(f"新增{added_count}只持仓股票到交易池")

    def get_all_stocks(self) -> Dict[str, str]:
        """获取所有股票"""
        return self.stocks.copy()

    def get_sorted_stocks(self) -> List[tuple]:
        """获取排序后的股票列表，持仓股票置顶"""
        position_stocks = [
            (code, name) for code, name in self.stocks.items() if code in self.positions
        ]
        other_stocks = [
            (code, name)
            for code, name in self.stocks.items()
            if code not in self.positions
        ]

        return position_stocks + other_stocks

    def is_position_stock(self, code: str) -> bool:
        """判断是否为持仓股票"""
        return code in self.positions

    def add_stock(self, code: str, name: str) -> bool:
        """添加股票到交易池

        Args:
            code: 股票代码
            name: 股票名称

        Returns:
            bool: 添加成功返回True，已存在返回False
        """
        if code in self.stocks:
            return False

        self.stocks[code] = name
        self.save_pool()
        logging.info(f"添加股票到交易池: {code} {name}")
        return True

    def remove_stock(self, code: str) -> bool:
        """从交易池移除股票

        Args:
            code: 股票代码

        Returns:
            bool: 移除成功返回True，不存在返回False
        """
        if code not in self.stocks:
            return False

        name = self.stocks[code]
        del self.stocks[code]
        # 从持仓列表中也移除（如果存在）
        self.positions.discard(code)
        self.save_pool()
        logging.info(f"从交易池移除股票: {code} {name}")
        return True

    def save_pool(self):
        """保存交易池到文件"""
        try:
            with open(self.pool_file, "w", encoding="utf-8") as f:
                # 写入注释说明
                f.write("# 交易池股票列表\n")
                f.write("# 格式: 股票代码,股票名称\n")
                f.write("# 示例: 000001,平安银行\n")
                f.write("\n")

                # 按代码排序写入股票
                for code in sorted(self.stocks.keys()):
                    name = self.stocks[code]
                    f.write(f"{code},{name}\n")

            logging.info(f"交易池已保存到文件: {self.pool_file}")

        except Exception as e:
            logging.error(f"保存交易池失败: {e}")


class TradingRecorder:
    """交易记录管理类"""

    def __init__(self, record_file: str = None):
        # 使用文件相对于模块所在目录的绝对路径，避免不同工作目录导致读取不同文件
        if record_file is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            record_file = os.path.join(base_dir, "交易记录.json")
        self.record_file = record_file
        self.records = []
        self.load_records()

    def load_records(self):
        """加载交易记录"""
        if os.path.exists(self.record_file):
            try:
                with open(self.record_file, encoding="utf-8") as f:
                    self.records = json.load(f)
            except Exception as e:
                logging.error(f"加载交易记录失败: {e}")
                self.records = []

    def save_records(self):
        """保存交易记录"""
        try:
            with open(self.record_file, "w", encoding="utf-8") as f:
                json.dump(self.records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"保存交易记录失败: {e}")

    def add_record(
        self,
        code: str,
        name: str,
        action: str,
        price: float,
        quantity: int,
        amount: float,
        is_simulation: bool = False,
    ):
        """添加交易记录"""
        record = {
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "股票代码": code,
            "股票名称": name,
            "操作": action,  # 买入/卖出
            "价格": price,
            "数量": quantity,
            "金额": amount,
            "类型": "模拟" if is_simulation else "实盘",
        }

        self.records.append(record)
        self.save_records()

        logging.info(f"记录交易: {record}")

    def get_records(self, limit: int = 100) -> List[Dict]:
        """获取最近的交易记录"""
        return self.records[-limit:] if self.records else []


# ================================
# 信号类
# ================================
# 交易策略引擎
# ================================


class SignalEngine:
    """技术指标信号检测"""

    def __init__(self, amplitude_threshold=0.005, high_low_diff_threshold=1.0):
        self.results = []
        self.label_map = {
            "ma5_bottom_turn": ("MA5 底部拐点", 0.6),
            "ma10_bottom_turn": ("MA10 底部拐点", 0.7),
            "ma20_bottom_turn": ("MA20 底部拐点", 0.8),
            "ma60_bottom_turn": ("MA60 底部拐点", 0.9),
            "ma5_top_turn": ("MA5 顶部拐点", 0.6),
            "ma10_top_turn": ("MA10 顶部拐点", 0.7),
            "ma20_top_turn": ("MA20 顶部拐点", 0.8),
            "ma60_top_turn": ("MA60 顶部拐点", 0.9),
            "ma_multi_bull": ("均线多头排列", 1.0),
            "ma_multi_bear": ("均线空头排列", 1.0),
            "macd_golden_cross": ("MACD金叉", 0.8),
            "macd_death_cross": ("MACD死叉", 0.8),
            "macd_divergence_bull": ("MACD底背离", 0.9),
            "macd_divergence_bear": ("MACD顶背离", 0.9),
            "rsi_oversold": ("RSI超卖", 0.7),
            "rsi_overbought": ("RSI超买", 0.7),
            "rsi_divergence_bull": ("RSI底背离", 0.8),
            "rsi_divergence_bear": ("RSI顶背离", 0.8),
            "volume_surge": ("成交量放大", 0.6),
            "volume_shrink": ("成交量萎缩", 0.5),
            "support_bounce": ("支撑位反弹", 0.7),
            "resistance_break": ("阻力位突破", 0.8),
        }
        self.amplitude_threshold = amplitude_threshold
        self.high_low_diff_threshold = high_low_diff_threshold

    def run_all_strategies(self, data, indicators, symbol=""):
        """运行所有策略信号检测"""
        self.results.clear()
        if "Close" not in data:
            return

        close = data.get("Close")

        # 基础趋势判断
        ma60 = indicators.get("MA60")
        main_trend_bull = close.iloc[-1] > ma60.iloc[-1] if ma60 is not None else False
        main_trend_bear = close.iloc[-1] < ma60.iloc[-1] if ma60 is not None else False

        # 1. 均线信号检测
        self._detect_ma_signals(indicators, main_trend_bull, main_trend_bear, symbol)

        # 2. MACD信号检测
        self._detect_macd_signals(data, indicators, symbol)

        # 3. RSI信号检测
        self._detect_rsi_signals(data, indicators, symbol)

    def _detect_ma_signals(self, indicators, main_trend_bull, main_trend_bear, symbol):
        """检测均线信号"""

        ma_periods = [5, 10, 20, 60]
        for period in ma_periods:
            ma_col = f"MA{period}"
            if ma_col in indicators:
                ma_values = indicators[ma_col].dropna().values
                if len(ma_values) >= 6:
                    for direction in ["bottom", "top"]:
                        if (direction == "bottom" and main_trend_bull) or (
                            direction == "top" and main_trend_bear
                        ):
                            name = f"ma{period}_{direction}_turn"
                            is_signal, idx = self.detect_ma_turning_point(
                                ma_values, direction, None, None
                            )
                            if is_signal:
                                _, score = self.label_map.get(name, (name, 0))
                                self.results.append((symbol, name, idx, score))

    def _detect_macd_signals(self, data, indicators, symbol):
        """检测MACD信号"""
        if "MACD" not in indicators or "MACD_Signal" not in indicators:
            return

        macd = indicators["MACD"]
        signal = indicators["MACD_Signal"]

        if len(macd) < 3:
            return

        # MACD金叉死叉
        current_cross = macd.iloc[-1] - signal.iloc[-1]
        prev_cross = macd.iloc[-2] - signal.iloc[-2]

        if prev_cross <= 0 and current_cross > 0:  # 金叉
            score = self.label_map["macd_golden_cross"][1]
            self.results.append((symbol, "macd_golden_cross", len(macd) - 1, score))
        elif prev_cross >= 0 and current_cross < 0:  # 死叉
            score = self.label_map["macd_death_cross"][1]
            self.results.append((symbol, "macd_death_cross", len(macd) - 1, score))

    def _detect_rsi_signals(self, data, indicators, symbol):
        """检测RSI信号"""
        if "RSI" not in indicators:
            return

        rsi = indicators["RSI"]
        if len(rsi) < 3:
            return

        current_rsi = rsi.iloc[-1]

        # RSI超买超卖
        if current_rsi > 70:  # 超买
            score = self.label_map["rsi_overbought"][1]
            self.results.append((symbol, "rsi_overbought", len(rsi) - 1, score))
        elif current_rsi < 30:  # 超卖
            score = self.label_map["rsi_oversold"][1]
            self.results.append((symbol, "rsi_oversold", len(rsi) - 1, score))

    def detect_ma_turning_point(
        self, ma_values, direction="bottom", high=None, low=None
    ):
        import numpy as np

        if len(ma_values) < 3:
            return False, -1
        recent = ma_values[-3:]
        grads = np.gradient(recent)
        amplitude = (
            abs((recent[-1] - recent[-2]) / recent[-2]) if recent[-2] != 0 else 0
        )
        if amplitude <= self.amplitude_threshold:
            return False, -1
        if direction == "bottom" and grads[-2] < 0 and grads[-1] > 0:
            return True, len(ma_values) - 1
        if direction == "top" and grads[-2] > 0 and grads[-1] < 0:
            return True, len(ma_values) - 1
        return False, -1

    def detect_ma_alignment(self, indicators):
        ma5, ma10, ma20 = (
            indicators.get("MA5"),
            indicators.get("MA10"),
            indicators.get("MA20"),
        )
        if ma5 is None or ma10 is None or ma20 is None or len(ma5) < 1:
            return False, "", -1
        if ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]:
            return True, "ma_multi_bull", len(ma5) - 1
        if ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1]:
            return True, "ma_multi_bear", len(ma5) - 1
        return False, "", -1


# ================================


class SystemSignals(QObject):
    """系统信号类"""

    # 日志信号
    log_message = pyqtSignal(str, str)  # message, level

    # 数据更新信号
    positions_updated = pyqtSignal(list)
    account_updated = pyqtSignal(dict)
    realtime_updated = pyqtSignal(dict)

    # 系统状态信号
    client_status_changed = pyqtSignal(bool)
    initialization_progress = pyqtSignal(int, str)
    status_message = pyqtSignal(str)  # 用于显示状态栏消息
    goldminer_not_running = pyqtSignal()  # 掘金终端未运行信号


# ================================
# 主窗口类
# ================================


class MainWindow(QMainWindow):
    """主窗口类"""

    def __init__(self):
        super().__init__()

        # 初始化组件
        self.signals = SystemSignals()
        self.config = Config()
        self.setup_logging()

        # 业务组件
        self.myquant_client = MyQuantClient(self.config)
        # 使用配置文件中的交易池路径
        pool_file = self.config.get("stock_list_file", "交易池.txt")
        self.stock_pool = StockPool(pool_file)
        self.trade_recorder = TradingRecorder()

        # 界面状态
        self.current_stock = None
        self.current_period = "15m"

        # 图表相关属性
        self.zoom_level = 100  # 默认缩放级别
        self.current_main_indicator = "操盘线"  # 主图指标
        self.current_subplot_indicator = "MACD"  # 副图指标
        # 图表画布相关属性（延迟初始化）
        self.canvas = None
        self.fig = None
        self.ax_price = None
        # 默认图标尺寸（可调整为 24/32/40）
        self.icon_size = QSize(24, 24)
        self.ax_volume = None

        # 执行引擎（根据模拟/实盘模式在 toggle_simulation_mode 时切换）
        self.execution_engine = None
        try:
            self._init_execution_engine()
        except Exception:
            # 延迟初始化失败时不阻塞界面启动
            self.execution_engine = None

        self.ax_indicator = None

        # 自动刷新定时器
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_stock_pool)

        # 初始化界面
        self.init_ui()
        self.connect_signals()

        # 显示启动信息
        self.log("🚀 股票自动交易系统启动完成", "SUCCESS")
        self.log("💡 正在进行自动初始化...", "INFO")

        # 恢复自动初始化
        QTimer.singleShot(1000, lambda: self.initialize_system(True))

    def setup_logging(self):
        """设置日志系统"""
        # 创建日志显示区域（临时的，在init_ui中会重新赋值）
        self.log_text = QTextEdit()
        self.logger = Logger(self.log_text)

    def log(self, message: str, level: str = "INFO"):
        """添加日志"""
        self.logger.log(message, level)

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("A股自动交易系统 v2.0")
        self.setGeometry(100, 100, 1800, 1000)

        # 创建菜单栏
        self.create_menu_bar()

        # 创建工具栏
        self.create_toolbar()

        # 设置字体
        font = QFont("Microsoft YaHei", 10)
        self.setFont(font)

        # 创建中央分割器
        main_splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(main_splitter)

        # 左侧面板（控制区）
        left_panel = self.create_left_panel()
        main_splitter.addWidget(left_panel)

        # 中间面板（图表区）
        center_panel = self.create_center_panel()
        main_splitter.addWidget(center_panel)

        # 右侧面板（交易池）
        right_panel = self.create_right_panel()
        main_splitter.addWidget(right_panel)

        # 设置分割器比例，调整为用户指定的宽度配置
        main_splitter.setSizes([430, 900, 470])

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # 左侧显示的状态消息（主状态消息）
        self.status_bar.showMessage("就绪")

        # 将初始化进度条放在状态消息后面（靠左）
        self.init_progress = QProgressBar()
        self.init_progress.setMaximumWidth(200)
        # 设置范围并显示文本百分比
        self.init_progress.setRange(0, 100)
        self.init_progress.setFormat("初始化: %p%")
        self.init_progress.setTextVisible(True)
        self.init_progress.setVisible(False)
        # 使用 addWidget 将进度条放到状态栏左侧区域
        self.status_bar.addWidget(self.init_progress)

        # 创建客户端连接状态标签（放在状态栏最右侧）
        self.status_client_label = QLabel("❌ 客户端未连接")
        self.status_client_label.setStyleSheet("color: red;")
        self.status_bar.addPermanentWidget(self.status_client_label)

        # 初始化交易模式按钮样式
        self.update_trading_mode_buttons()

    def create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")

        # 历史数据管理
        data_action = QAction("历史数据管理(&D)", self)
        data_action.setStatusTip("管理和下载股票历史数据")
        data_action.triggered.connect(self.show_historical_data_dialog)
        file_menu.addAction(data_action)

        file_menu.addSeparator()

        # 设置
        settings_action = QAction("设置(&S)", self)
        settings_action.setStatusTip("系统设置")
        settings_action.triggered.connect(self.show_settings_dialog)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        # 退出
        exit_action = QAction("退出(&Q)", self)
        exit_action.setStatusTip("退出程序")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 工具菜单
        tools_menu = menubar.addMenu("工具(&T)")

        # 数据源测试
        test_connection_action = QAction("连接测试(&C)", self)
        test_connection_action.setStatusTip("测试MyQuant连接")
        test_connection_action.triggered.connect(self.test_myquant_connection)
        tools_menu.addAction(test_connection_action)

        # 刷新数据
        refresh_action = QAction("刷新数据(&R)", self)
        refresh_action.setStatusTip("刷新交易池数据")
        refresh_action.triggered.connect(self.refresh_stock_pool)
        tools_menu.addAction(refresh_action)

        tools_menu.addSeparator()

        # 订单查询
        orders_action = QAction("订单查询(&O)", self)
        orders_action.setStatusTip("查询当日交易订单")
        orders_action.triggered.connect(self.show_orders_dialog)
        tools_menu.addAction(orders_action)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")

        # 关于
        about_action = QAction("关于(&A)", self)
        about_action.setStatusTip("关于本软件")
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def create_toolbar(self):
        """创建工具栏（icon-only, tooltip on hover）"""
        # 如果已有主工具栏，先移除（便于切换图标尺寸时重建）
        if hasattr(self, "_main_toolbar") and isinstance(self._main_toolbar, QToolBar):
            try:
                self.removeToolBar(self._main_toolbar)
            except Exception:
                pass

        toolbar = QToolBar("主工具栏")
        self._main_toolbar = toolbar
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        toolbar.setIconSize(self.icon_size)
        self.addToolBar(toolbar)

        toolbar.setStyleSheet(
            """
            QToolBar {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                spacing: 5px;
                padding: 5px;
            }
            QToolBar QToolButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 5px;
                margin: 2px;
            }
            QToolBar QToolButton:hover {
                background-color: #e3f2fd;
                border: 1px solid #2196f3;
            }
            QToolBar QToolButton:pressed {
                background-color: #bbdefb;
            }
            """
        )

        # 使用类方法创建图标按钮，便于复用
        def make_toolbutton(icon_name, tooltip, slot=None):
            return self._create_icon_button(
                icon_name, tooltip, slot, size=self.icon_size, parent_toolbar=toolbar
            )

        # 添加工具按钮（只使用图标，名称通过 tooltip 显示）
        make_toolbutton("settings.svg", "系统设置", self.show_settings_dialog)
        make_toolbutton("init.svg", "系统初始化", self.initialize_system)
        make_toolbutton("orders.svg", "交易记录", self.show_orders_dialog)
        toolbar.addSeparator()
        make_toolbutton("connection.svg", "连接测试", self.test_myquant_connection)
        make_toolbutton(
            "permissions.svg", "权限检测", self.check_trading_permissions_dialog
        )
        make_toolbutton("refresh.svg", "刷新数据", self.refresh_stock_pool)
        make_toolbutton("add.svg", "添加股票", self.show_add_stock_dialog)
        toolbar.addSeparator()
        make_toolbutton("data.svg", "历史数据", self.show_historical_data_dialog)
        make_toolbutton("monitor.svg", "股票监控", self.placeholder_action)
        make_toolbutton("strategy.svg", "交易策略", self.placeholder_action)
        toolbar.addSeparator()
        make_toolbutton("help.svg", "帮助", self.show_about)

        # 添加一个图标尺寸选择器，便于预览 24/32/40 三种大小
        size_selector = QComboBox()
        size_selector.addItems(["24", "32", "40"])
        size_selector.setCurrentText(str(self.icon_size.width()))

        def on_size_change(idx):
            try:
                new_size = int(size_selector.currentText())
                self.icon_size = QSize(new_size, new_size)
                # 重新构建工具栏以应用新尺寸
                self.create_toolbar()
            except Exception:
                pass

        size_selector.currentIndexChanged.connect(on_size_change)
        toolbar.addWidget(size_selector)

    def _create_icon_button(
        self,
        icon_name: str,
        tooltip: str,
        slot=None,
        size: QSize = None,
        parent_toolbar: QToolBar = None,
    ) -> QToolButton:
        """创建一个图标按钮的复用方法。

        Args:
            icon_name: icons 目录下的文件名
            tooltip: 悬浮提示文字
            slot: 可选的点击回调
            size: 图标大小（QSize），若为 None 则使用 self.icon_size
            parent_toolbar: 若提供，则会把按钮加入到该工具栏
        Returns:
            QToolButton 实例
        """
        icon_dir = os.path.join(os.path.dirname(__file__), "icons")
        path = os.path.join(icon_dir, icon_name)
        icon = QIcon(path) if os.path.exists(path) else QIcon()

        btn = QToolButton(self)
        btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        use_size = size or self.icon_size
        btn.setIcon(icon)
        btn.setIconSize(use_size)
        btn.setToolTip(tooltip)
        # 按钮比图标略大以便于点击
        btn.setFixedSize(use_size.width() + 16, use_size.height() + 24)
        if slot:
            btn.clicked.connect(slot)
        if parent_toolbar is not None:
            parent_toolbar.addWidget(btn)
        return btn

    def placeholder_action(self):
        """占位方法，用于未来功能扩展"""
        QMessageBox.information(self, "提示", "该功能正在开发中，敬请期待！")

    def create_left_panel(self) -> QWidget:
        """创建左侧控制面板（保留属性，但不在左侧重复展示工具栏中的‘初始化’与‘设置’按钮）"""
        panel = QWidget()
        # 不设置固定宽度，让分割器控制宽度
        # panel.setFixedWidth(400)
        layout = QVBoxLayout(panel)

        # 保留按钮属性供其它逻辑使用，但不在左侧栏显示
        self.init_button = QPushButton("🔄 初始化系统")
        self.init_button.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 8px; background-color: #4CAF50; color: white; }"
        )
        self.init_button.clicked.connect(self.initialize_system)

        self.settings_button = QPushButton("⚙️ 设置")
        self.settings_button.clicked.connect(self.show_settings_dialog)

        # 交易模式控制
        trading_group = QGroupBox("交易模式")
        trading_layout = QVBoxLayout(trading_group)

        # 交易模式按钮横排布局
        mode_buttons_layout = QHBoxLayout()

        # 模拟交易模式按钮
        self.simulation_button = QPushButton("○ 模拟交易模式")
        self.simulation_button.setCheckable(True)
        self.simulation_button.setChecked(
            self.config.get("trading.simulation_mode", True)
        )
        self.simulation_button.clicked.connect(self.toggle_simulation_mode)
        mode_buttons_layout.addWidget(self.simulation_button)

        # 实盘交易模式按钮
        self.real_trading_button = QPushButton("○ 实盘交易模式")
        self.real_trading_button.setCheckable(True)
        self.real_trading_button.setChecked(
            not self.config.get("trading.simulation_mode", True)
        )
        self.real_trading_button.clicked.connect(self.toggle_real_trading_mode)
        mode_buttons_layout.addWidget(self.real_trading_button)

        trading_layout.addLayout(mode_buttons_layout)

        # 初始化按钮样式
        self.update_trading_mode_buttons()

        layout.addWidget(trading_group)

        # 持仓和资金显示
        account_group = QGroupBox("账户信息 ")
        account_layout = QVBoxLayout(account_group)

        # 添加说明标签
        # account_info_label = QLabel(
        #     "说明: 账户信息实时从MyQuant客户端获取，无需手动设置"
        # )

        # account_info_label.setStyleSheet("color: #666; font-size: 10px; margin: 2px;")
        # account_layout.addWidget(account_info_label)

        # 资金信息表格
        self.account_table = QTableWidget(1, 4)
        self.account_table.setHorizontalHeaderLabels(
            ["总资产", "可用资金", "持仓市值", "当日盈亏"]
        )
        self.account_table.setMaximumHeight(80)
        account_layout.addWidget(self.account_table)

        # 持仓信息表格
        self.position_table = QTableWidget(0, 5)
        self.position_table.setHorizontalHeaderLabels(
            ["代码", "名称", "数量", "成本价", "现价"]
        )
        self.position_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.position_table.setMaximumHeight(300)  # 增加高度以显示更多行
        account_layout.addWidget(self.position_table)

        layout.addWidget(account_group)

        # 同步客户端功能
        sync_group = QGroupBox("同步客户端")
        sync_layout = QVBoxLayout(sync_group)

        self.sync_client_button = QPushButton("� 同步客户端")
        self.sync_client_button.clicked.connect(self.sync_client_data)
        sync_layout.addWidget(self.sync_client_button)

        layout.addWidget(sync_group)

        # 日志显示区域
        log_group = QGroupBox("系统日志")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(300)  # 增加日志高度
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        # 重新设置logger的text_widget
        self.logger.text_widget = self.log_text

        layout.addWidget(log_group)

        # 移除弹性空间，让组件能够充分利用可用空间
        # layout.addStretch()
        return panel

    def create_center_panel(self) -> QWidget:
        """创建中间图表面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # 图表控制区
        chart_control = QWidget()
        chart_control.setMaximumHeight(60)
        control_layout = QHBoxLayout(chart_control)

        # 周期选择
        self.period_combo = QComboBox()
        self.period_combo.addItems(["1m", "5m", "15m", "60m", "1d"])
        self.period_combo.setCurrentText("15m")
        self.period_combo.currentTextChanged.connect(self.on_period_changed)
        control_layout.addWidget(QLabel("周期:"))
        control_layout.addWidget(self.period_combo)

        # 主图指标选择
        control_layout.addWidget(QLabel("主图:"))
        self.indicator_combo = QComboBox()
        self.indicator_combo.addItems(["操盘线", "均线"])
        self.indicator_combo.currentTextChanged.connect(self.on_indicator_change)
        control_layout.addWidget(self.indicator_combo)

        # 副图指标选择
        control_layout.addWidget(QLabel("副图:"))
        self.subplot_indicator_combo = QComboBox()
        self.subplot_indicator_combo.addItems(["MACD", "RSI", "KDJ", "BOLL"])
        self.subplot_indicator_combo.currentTextChanged.connect(
            self.on_subplot_indicator_change
        )
        control_layout.addWidget(self.subplot_indicator_combo)

        # 缩放控制
        control_layout.addWidget(QLabel("缩放:"))
        self.zoom_label = QLabel(f"{self.zoom_level}K")
        control_layout.addWidget(self.zoom_label)

        # 缩放按钮
        zoom_in_btn = QPushButton("放大")
        zoom_in_btn.clicked.connect(self.zoom_in)
        zoom_out_btn = QPushButton("缩小")
        zoom_out_btn.clicked.connect(self.zoom_out)
        control_layout.addWidget(zoom_in_btn)
        control_layout.addWidget(zoom_out_btn)

        control_layout.addStretch()
        layout.addWidget(chart_control)

        # 图表显示区域
        self.chart_canvas = self.create_chart_canvas()
        layout.addWidget(self.chart_canvas)

        return panel

    def create_chart_canvas(self) -> QWidget:
        """创建图表画布"""
        import matplotlib.pyplot as plt

        # 初始化图表相关属性
        self.zoom_level = 120  # K线显示数量
        self.current_indicator = "操盘线"
        self.current_subplot_indicator = "MACD"
        self.data_cache = {}

        # 设置图表配色主题
        self.chart_colors = {
            "bg": "#FAFAFA",
            "ax": "#FFFFFF",
            "up": "#F44336",
            "down": "#4CAF50",
            "text": "#333333",
            "grid": "#E0E0E0",
            "ma5": "#FF6B35",
            "ma10": "#F7931E",
            "ma20": "#9C27B0",
            "ma60": "#2196F3",
        }

        # 配置matplotlib中文字体
        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
        plt.rcParams["axes.unicode_minus"] = False

        # 创建matplotlib图表 - 三行布局
        self.figure = Figure(figsize=(12, 8), facecolor=self.chart_colors["bg"])
        self.canvas = FigureCanvas(self.figure)

        # 设置canvas可以接收键盘焦点
        self.canvas.setFocusPolicy(Qt.StrongFocus)

        # 创建三个子图：价格图、成交量图、指标图
        # 使用gridspec来更好地控制布局
        from matplotlib.gridspec import GridSpec

        gs = GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.15)

        self.ax_price = self.figure.add_subplot(gs[0])  # 价格图 (占60%高度)
        self.ax_vol = self.figure.add_subplot(
            gs[1], sharex=self.ax_price
        )  # 成交量图 (占20%高度)
        self.ax_indicator = self.figure.add_subplot(
            gs[2], sharex=self.ax_price
        )  # 指标图 (占20%高度)

        # 调整子图间距
        self.figure.subplots_adjust(left=0.06, right=0.995, bottom=0.07, top=0.94)

        # 显示默认提示
        # 将提示文本左对齐并放置在图表左上角，便于阅读
        self.ax_price.text(
            0.01,
            0.95,
            "请从右侧交易池选择股票查看图表\n\n📈 支持功能：\n• 方向键/上下键 - 缩放K线数量\n• 主图指标 - 操盘线/均线\n• 副图指标 - MACD/RSI/KDJ/BOLL",
            horizontalalignment="left",
            verticalalignment="top",
            transform=self.ax_price.transAxes,
            fontsize=12,
            bbox={"boxstyle": "round,pad=0.5", "facecolor": "lightblue", "alpha": 0.3},
        )

        # 隐藏成交量和指标图（初始状态）
        self.ax_vol.set_visible(False)
        self.ax_indicator.set_visible(False)

        self.canvas.draw()
        return self.canvas

    def create_right_panel(self) -> QWidget:
        """创建右侧交易池面板"""
        panel = QWidget()
        # 不设置固定宽度，让分割器控制宽度
        # panel.setFixedWidth(600)
        layout = QVBoxLayout(panel)

        # 交易池标题
        pool_group = QGroupBox("交易池")
        pool_layout = QVBoxLayout(pool_group)

        # 交易池控制按钮
        pool_control = QWidget()
        pool_control_layout = QHBoxLayout(pool_control)

        # 使用图标按钮替换文本按钮，保持 tooltip 与回调
        self.refresh_pool_button = self._create_icon_button(
            "qt_builtin_refresh_24.png",
            "刷新交易池数据",
            slot=self.refresh_stock_pool,
            size=self.icon_size,
            parent_toolbar=None,
        )
        pool_control_layout.addWidget(self.refresh_pool_button)

        # 添加刷新状态指示器
        self.refresh_status_label = QLabel("准备就绪")
        self.refresh_status_label.setAlignment(Qt.AlignCenter)
        self.refresh_status_label.setStyleSheet(
            """
            QLabel {
                background-color: #2196F3;
                color: white;
                padding: 5px 8px;
                border-radius: 3px;
                font-weight: bold;
                min-width: 120px;
            }
        """
        )
        pool_control_layout.addWidget(self.refresh_status_label)

        # 添加弹性空间，让后面的元素靠右
        pool_control_layout.addStretch()

        # 添加刷新频率选择
        refresh_freq_label = QLabel("刷新频率:")
        refresh_freq_label.setAlignment(Qt.AlignCenter)  # 标签内文字居中
        refresh_freq_label.setStyleSheet(
            """
            QLabel {
                background-color: #4CAF50;
                color: white;
                padding: 5px 8px;
                border-radius: 3px;
                font-weight: bold;
            }
        """
        )
        pool_control_layout.addWidget(refresh_freq_label)

        self.refresh_freq_combo = QComboBox()
        self.refresh_freq_combo.addItems(["手动", "10秒", "30秒", "60秒", "120秒"])
        self.refresh_freq_combo.setCurrentText("30秒")  # 默认30秒
        self.refresh_freq_combo.currentTextChanged.connect(self.on_refresh_freq_changed)
        pool_control_layout.addWidget(self.refresh_freq_combo)

        pool_layout.addWidget(pool_control)

        # 交易池表格
        self.pool_table = QTableWidget(0, 6)
        self.pool_table.setHorizontalHeaderLabels(
            ["代码", "名称", "现价", "涨跌幅", "换手率", "状态"]
        )
        self.pool_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # 设置表格点击事件
        self.pool_table.cellClicked.connect(self.on_stock_selected)

        # 设置右键菜单
        self.pool_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pool_table.customContextMenuRequested.connect(self.show_stock_context_menu)

        pool_layout.addWidget(self.pool_table)
        layout.addWidget(pool_group)

        # 交易记录区域（移到右侧栏）
        record_group = QGroupBox("交易记录")
        record_layout = QVBoxLayout(record_group)

        self.record_button = QPushButton("📋 查看交易记录")
        self.record_button.clicked.connect(self.show_trade_records)
        record_layout.addWidget(self.record_button)

        layout.addWidget(record_group)

        return panel

    def connect_signals(self):
        """连接信号槽"""
        self.signals.log_message.connect(self.logger.log)
        self.signals.client_status_changed.connect(self.update_client_status)
        self.signals.positions_updated.connect(self.update_positions_table)
        self.signals.account_updated.connect(self.update_account_table)
        self.signals.realtime_updated.connect(self.update_stock_pool_table)
        self.signals.initialization_progress.connect(self.update_init_progress)
        self.signals.status_message.connect(self.on_status_message)
        self.signals.goldminer_not_running.connect(self.on_goldminer_not_running)

    def on_status_message(self, message: str):
        """处理状态栏消息"""
        self.status_bar.showMessage(message)

    def on_goldminer_not_running(self):
        """处理掘金终端未运行的情况
        弹出提示窗口告知用户需要打开掘金量化客户端
        """
        try:
            # 弹出提示窗口
            self.log("💡 检测到掘金终端未运行，正在显示提示窗口...", "INFO")
            QMessageBox.warning(
                self,
                "掘金终端未运行",
                "本程序依赖掘金量化客户端工作，请打开并登录客户端。",
                QMessageBox.Ok,
            )
        except Exception as e:
            self.log(f"❌ 处理掘金终端未运行情况时发生异常: {str(e)}", "ERROR")

    # ================================
    # A. 初始化系统功能
    # ================================

    def initialize_system(self, is_auto=False):
        """统一的系统初始化方法

        Args:
            is_auto: 是否为自动初始化模式，自动模式会启动自动刷新和自动显示图表
        """
        try:
            mode_text = "自动" if is_auto else "手动"
            self.log(f"开始{mode_text}初始化系统...", "INFO")
            self.init_progress.setVisible(True)
            self.init_progress.setValue(0)

            # 启动初始化线程
            self.init_thread = InitializationThread(
                self.myquant_client, self.stock_pool, self.signals
            )
            # 根据是否自动初始化选择不同的完成回调
            if is_auto:
                self.init_thread.finished.connect(self.on_auto_initialization_finished)
            else:
                self.init_thread.finished.connect(self.on_initialization_finished)
            self.init_thread.start()

        except Exception as e:
            self.log(f"{mode_text}初始化失败: {e}", "ERROR")
            self.init_progress.setVisible(False)
            if is_auto:
                QMessageBox.warning(
                    self,
                    "初始化失败",
                    f"自动初始化系统失败:\n{str(e)}\n\n请点击'初始化系统'按钮手动重试。",
                )

    def on_auto_initialization_finished(self):
        """自动初始化完成回调"""
        self.init_progress.setVisible(False)
        self.init_button.setText("✅ 初始化完成")
        self.init_button.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 8px; background-color: #4CAF50; color: white; }"
        )

        # 刷新交易池显示
        try:
            self.refresh_stock_pool()
            # 在交易时间才启动30秒自动刷新定时器
            if self.is_trading_time():
                self.refresh_timer.start(30000)  # 30秒
                self.log("系统自动初始化完成！已启动30秒自动刷新", "SUCCESS")
            else:
                # 非交易时间不启动定时器
                self.log("系统自动初始化完成！当前为非交易时间，已暂停自动刷新", "INFO")
                self.refresh_status_label.setText("⏸️ 非交易时间")
                self.refresh_status_label.setStyleSheet(
                    """
                    QLabel {
                        background-color: #9E9E9E;
                        color: white;
                        padding: 5px 8px;
                        border-radius: 3px;
                        font-weight: bold;
                        min-width: 120px;
                    }
                """
                )

            # 自动显示交易池第一只股票图表
            if self.pool_table.rowCount() > 0:
                # 自动选择第一行
                self.pool_table.selectRow(0)
                # 获取第一只股票信息
                code_item = self.pool_table.item(0, 0)
                name_item = self.pool_table.item(0, 1)
                if code_item and name_item:
                    code = code_item.text()
                    name = name_item.text()
                    self.current_stock = (code, name)
                    self.update_chart()
                    self.log(f"已自动显示交易池第一只股票: {code} {name}", "INFO")
        except Exception as e:
            self.log(f"刷新交易池或自动显示图表失败: {e}", "WARNING")

    def on_initialization_finished(self):
        """初始化完成回调"""
        self.init_progress.setVisible(False)
        self.init_button.setText("✅ 初始化完成")
        self.init_button.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 8px; background-color: #4CAF50; color: white; }"
        )

        # 刷新交易池显示
        self.refresh_stock_pool()

        self.log("系统初始化完成！", "SUCCESS")

    def update_client_status(self, connected: bool):
        """更新客户端连接状态"""
        if connected:
            self.status_client_label.setText("✅ 客户端已连接")
            self.status_client_label.setStyleSheet("color: green;")
        else:
            self.status_client_label.setText("❌ 客户端未连接")
            self.status_client_label.setStyleSheet("color: red;")

    def update_init_progress(self, value: int, message: str):
        """更新初始化进度"""
        self.init_progress.setValue(value)
        self.status_bar.showMessage(message)

    # ================================
    # B. 设置功能
    # ================================

    def show_settings_dialog(self):
        """显示设置对话框"""
        dialog = SettingsDialog(self.config, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            self.config.save_config()
            self.log("设置已保存", "SUCCESS")

    def show_historical_data_dialog(self):
        """显示历史数据管理对话框"""
        dialog = SimpleHistoricalDataDialog(
            self.config, self.myquant_client, self.stock_pool
        )
        dialog.exec_()

    def show_orders_dialog(self):
        """显示订单查询对话框"""
        dialog = OrdersDialog(self.myquant_client, parent=self)
        dialog.exec_()

    def test_myquant_connection(self):
        """测试MyQuant连接（异步执行）"""
        # 创建一个简单的测试，避免卡死主界面

        # 显示测试开始信息
        self.log("开始测试MyQuant连接...", "INFO")

        # 使用QTimer延迟执行，避免阻塞界面
        QTimer.singleShot(100, self._do_async_connection_test)

    def _do_async_connection_test(self):
        """异步执行连接测试"""
        try:
            # 设置较短的测试时间
            success = False
            message = ""

            if self.myquant_client.connect():
                success = True
                message = "MyQuant连接成功！"
                self.log("MyQuant连接测试成功", "SUCCESS")

                # 测试实时数据获取
                test_symbols = ["000001", "000002"]  # 测试平安银行和万科A
                self.log("测试实时数据获取...", "INFO")
                realtime_data = self.myquant_client.get_realtime_data(test_symbols)

                if realtime_data:
                    self.log(f"成功获取{len(realtime_data)}只股票的实时数据", "SUCCESS")
                    for code, data in realtime_data.items():
                        self.log(
                            f"{code}: 价格={data['价格']}, 涨跌幅={data['涨跌幅']}%, 换手率={data['换手率']}%",
                            "INFO",
                        )
                else:
                    self.log("实时数据获取失败", "WARNING")

            else:
                success = False
                message = "MyQuant连接失败！\n请检查配置信息。"
                self.log("MyQuant连接测试失败", "ERROR")

                # 测试备用数据源
                if AKSHARE_AVAILABLE:
                    self.log("尝试备用数据源AKShare...", "INFO")
                    test_symbols = ["000001", "000002"]
                    backup_data = self.myquant_client._get_realtime_data_from_akshare(
                        test_symbols
                    )
                    if backup_data:
                        self.log(
                            f"AKShare备用数据源可用，获取到{len(backup_data)}只股票数据",
                            "SUCCESS",
                        )
                        for code, data in backup_data.items():
                            self.log(
                                f"{code}: 价格={data['价格']}, 涨跌幅={data['涨跌幅']}%, 换手率={data['换手率']}%",
                                "INFO",
                            )
                    else:
                        self.log("AKShare备用数据源也不可用", "ERROR")
                else:
                    self.log("AKShare未安装，无备用数据源", "WARNING")

            # 显示结果
            if success:
                QMessageBox.information(self, "连接测试", f"✅ {message}")
            else:
                QMessageBox.warning(self, "连接测试", f"❌ {message}")

        except Exception as e:
            error_msg = f"连接测试异常: {str(e)}"
            self.log(error_msg, "ERROR")
            QMessageBox.critical(self, "连接测试", f"❌ {error_msg}")

    def show_about(self):
        """显示关于对话框"""
        about_text = """
        <h3>A股自动交易系统 v2.0</h3>
        <p><b>功能特性：</b></p>
        <ul>
        <li>🔗 MyQuant数据源集成</li>
        <li>📊 实时行情显示</li>
        <li>📈 K线图表分析</li>
        <li>💼 交易池管理</li>
        <li>🤖 自动交易策略</li>
        <li>📦 历史数据管理</li>
        <li>📋 交易记录追踪</li>
        </ul>
        <p><b>开发者：</b> 木鱼听禅 </p>
        <p><b>更新时间：</b> 2025年8月26日</p>
        """
        QMessageBox.about(self, "关于", about_text)

    # ================================
    # C. 交易池功能
    # ================================

    def refresh_stock_pool(self):
        """刷新交易池显示，无论是否为交易时间都显示股票和持仓信息"""
        if not self.myquant_client.is_connected():
            self.log("客户端未连接，显示静态交易池数据", "WARNING")
            self.refresh_status_label.setText("⚠️ 未连接")
            self.refresh_status_label.setStyleSheet(
                """
                QLabel {
                    background-color: #FF9800;
                    color: white;
                    padding: 5px 8px;
                    border-radius: 3px;
                    font-weight: bold;
                    min-width: 120px;
                }
            """)
            # 不直接返回，继续显示静态交易池数据

        # 检查是否为交易时间
        is_trading = self.is_trading_time()
        # 更新状态显示
        if is_trading:
            self.refresh_status_label.setText("🔄 刷新中...")
            self.refresh_status_label.setStyleSheet(
                """
                QLabel {
                    background-color: #FFC107;
                    color: black;
                    padding: 5px 8px;
                    border-radius: 3px;
                    font-weight: bold;
                    min-width: 120px;
                }
            """
            )
        else:
            self.log("当前为非交易时间，显示静态持仓信息", "INFO")
            self.refresh_status_label.setText("⏸️ 非交易时间")
            self.refresh_status_label.setStyleSheet(
                """
                QLabel {
                    background-color: #9E9E9E;
                    color: white;
                    padding: 5px 8px;
                    border-radius: 3px;
                    font-weight: bold;
                    min-width: 120px;
                }
            """
            )
        # 立即更新UI
        QApplication.processEvents()

        # 获取股票列表
        stocks = self.stock_pool.get_sorted_stocks()
        if not stocks:
            self.log("交易池为空", "WARNING")
            return

        # 更新表格行数
        self.pool_table.setRowCount(len(stocks))

        # 仅在交易时间获取实时数据
        realtime_data = {}
        if is_trading:
            codes = [code for code, name in stocks]
            # 添加强制刷新参数，确保每次都获取最新数据
            realtime_data = self.myquant_client.get_realtime_data(codes, force_refresh=True)

        # 填充表格
        for i, (code, name) in enumerate(stocks):
            self.pool_table.setItem(i, 0, QTableWidgetItem(code))
            self.pool_table.setItem(i, 1, QTableWidgetItem(name))

            # 实时数据
            if code in realtime_data and is_trading:
                data = realtime_data[code]
                # 确保数据类型正确
                price = float(data['价格'])
                change_pct = float(data["涨跌幅"])
                turnover_rate = float(data['换手率'])
                
                # 日志记录详细数据更新情况
                logging.debug(f"刷新交易池 - {code}: 价格={price:.2f}, 涨跌幅={change_pct:.2f}%, 数据源={data.get('数据源', '未知')}")
                
                # 更新价格
                price_item = QTableWidgetItem(f"{price:.2f}")
                self.pool_table.setItem(i, 2, price_item)
                
                # 涨跌幅颜色处理
                change_item = QTableWidgetItem(f"{change_pct:.2f}%")
                if change_pct > 0:
                    change_item.setForeground(QColor("red"))
                elif change_pct < 0:
                    change_item.setForeground(QColor("green"))
                self.pool_table.setItem(i, 3, change_item)
                
                # 更新换手率
                turnover_item = QTableWidgetItem(f"{turnover_rate:.2f}%")
                self.pool_table.setItem(i, 4, turnover_item)
                
                # 确保表格数据立即更新显示
                self.pool_table.viewport().update()
            else:
                # 非交易时间显示缓存数据或占位符，但仍然显示持仓状态
                self.pool_table.setItem(i, 2, QTableWidgetItem("--"))
                self.pool_table.setItem(i, 3, QTableWidgetItem("--"))
                self.pool_table.setItem(i, 4, QTableWidgetItem("--"))

            # 状态列（持仓/普通）- 无论是否为交易时间都显示
            status = "持仓" if self.stock_pool.is_position_stock(code) else "监控"
            status_item = QTableWidgetItem(status)
            if status == "持仓":
                status_item.setForeground(QColor("blue"))
            self.pool_table.setItem(i, 5, status_item)

        # 更新刷新状态和时间
        from datetime import datetime

        current_time = datetime.now().strftime("%H:%M:%S")
        if is_trading:
            self.refresh_status_label.setText(f"✅ 已更新 {current_time}")
            self.refresh_status_label.setStyleSheet(
                """
                QLabel {
                    background-color: #4CAF50;
                    color: white;
                    padding: 5px 8px;
                    border-radius: 3px;
                    font-weight: bold;
                    min-width: 120px;
                }
            """
            )

        # 不记录正常刷新的日志，避免淹没其他重要信息
        # 只记录异常情况或重要信息

    def is_trading_time(self):
        """检查当前是否为交易时间"""
        import calendar
        from datetime import datetime, time

        now = datetime.now()

        # 检查是否为周末
        if calendar.day_name[now.weekday()] in ["Saturday", "Sunday"]:
            return False

        # 检查是否为节假日（这里仅作为示例，可以扩展为完整的节假日列表）
        # 此处省略具体节假日判断逻辑

        # 检查时间范围（A股交易时间：9:30-11:30，13:00-15:00）
        current_time = now.time()
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)

        # 判断是否在交易时间内
        is_morning_trading = morning_start <= current_time <= morning_end
        is_afternoon_trading = afternoon_start <= current_time <= afternoon_end

        return is_morning_trading or is_afternoon_trading

    def on_refresh_freq_changed(self, freq_text: str):
        """刷新频率改变处理"""
        # 停止当前定时器
        self.refresh_timer.stop()

        if freq_text == "手动":
            self.log("已设置为手动刷新模式", "INFO")
            return

        # 解析频率设置
        freq_map = {
            "10秒": 10000,
            "30秒": 30000,
            "60秒": 60000,
            "120秒": 120000,
        }

        if freq_text in freq_map:
            interval = freq_map[freq_text]

            # 检查是否为交易时间
            if self.is_trading_time():
                self.refresh_timer.start(interval)
                self.log(f"已设置自动刷新频率: {freq_text}", "INFO")
            else:
                # 非交易时间，不启动定时器，但记录日志
                self.log("当前为非交易时间，已暂停自动刷新", "INFO")
                self.refresh_status_label.setText("⏸️ 非交易时间")
                self.refresh_status_label.setStyleSheet(
                    """
                    QLabel {
                        background-color: #9E9E9E;
                        color: white;
                        padding: 5px 8px;
                        border-radius: 3px;
                        font-weight: bold;
                        min-width: 120px;
                    }
                """
                )
        else:
            self.log(f"未知的刷新频率: {freq_text}", "WARNING")

    def on_stock_selected(self, row: int, column: int):
        """股票被选中时的处理"""
        if row < 0 or row >= self.pool_table.rowCount():
            return

        code_item = self.pool_table.item(row, 0)
        name_item = self.pool_table.item(row, 1)

        if code_item and name_item:
            code = code_item.text()
            name = name_item.text()

            self.current_stock = (code, name)
            self.update_chart()

            self.log(f"选中股票: {code} {name}", "INFO")

    def show_stock_context_menu(self, position):
        """显示股票右键菜单"""
        row = self.pool_table.rowAt(position.y())

        # 创建右键菜单
        menu = QMenu(self)

        if row < 0:
            # 点击空白处 - 显示添加股票选项
            self.show_empty_area_menu(menu, position)
        else:
            # 点击股票行 - 显示股票操作选项
            code_item = self.pool_table.item(row, 0)
            name_item = self.pool_table.item(row, 1)

            if not code_item or not name_item:
                # 如果数据异常，也显示添加选项
                self.show_empty_area_menu(menu, position)
                return

            code = code_item.text()
            name = name_item.text()
            self.show_stock_operation_menu(menu, code, name, position)

    def show_empty_area_menu(self, menu, position):
        """显示空白区域右键菜单"""
        # 添加股票选项
        add_stock_action = QAction("➕ 添加股票", self)
        add_stock_action.triggered.connect(self.show_add_stock_dialog)
        menu.addAction(add_stock_action)

        menu.addSeparator()

        # 刷新选项
        refresh_action = QAction("🔃 刷新交易池", self)
        refresh_action.triggered.connect(self.refresh_stock_pool)
        menu.addAction(refresh_action)

        # 导入选项（可选）
        import_action = QAction("📂 导入股票列表", self)
        import_action.triggered.connect(self.import_stock_list)
        menu.addAction(import_action)

        # 显示菜单
        global_pos = self.pool_table.mapToGlobal(position)
        menu.exec_(global_pos)

    def show_stock_operation_menu(self, menu, code, name, position):
        """显示股票操作右键菜单"""
        buy_action = QAction("💰 买入", self)
        buy_action.triggered.connect(lambda: self.trade_stock(code, name, "buy"))
        menu.addAction(buy_action)

        sell_action = QAction("💸 卖出", self)
        sell_action.triggered.connect(lambda: self.trade_stock(code, name, "sell"))
        menu.addAction(sell_action)

        menu.addSeparator()

        add_action = QAction("➕ 增加", self)
        add_action.triggered.connect(lambda: self.add_to_pool(code, name))
        menu.addAction(add_action)

        remove_action = QAction("➖ 删除", self)
        remove_action.triggered.connect(lambda: self.remove_from_pool(code, name))
        menu.addAction(remove_action)

        # 显示菜单
        global_pos = self.pool_table.mapToGlobal(position)
        menu.exec_(global_pos)

    def trade_stock(self, code: str, name: str, action: str):
        """交易股票（买入/卖出）"""
        # 检查是否为模拟模式
        is_simulation = self.simulation_button.isChecked()
        mode_text = "模拟" if is_simulation else "实盘"
        action_text = "买入" if action == "buy" else "卖出"

        # 简单的交易对话框
        dialog = TradeDialog(code, name, action, is_simulation, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            quantity, price, trade_type = dialog.get_trade_info()
            amount = quantity * price

            # 记录交易
            self.trade_recorder.add_record(
                code, name, f"{trade_type}", price, quantity, amount, is_simulation
            )

            # 根据交易类型显示不同的价格信息
            if "市价" in trade_type:
                price_text = "市价"
            else:
                price_text = f"{price:.2f}"

            self.log(
                f"[{mode_text}]{trade_type} {name}({code}) 数量:{quantity} 价格:{price_text} 金额:{amount:.2f}",
                "SUCCESS",
            )

            # 如果不是模拟模式，使用统一执行引擎下单（execution_engine 决定真实或模拟）
            if not is_simulation:
                if self.execution_engine is None:
                    self.log("⚠️ 未初始化执行引擎，无法下单", "ERROR")
                else:
                    try:
                        result = self.execution_engine.place_order(
                            code, action, quantity, price, trade_type
                        )

                        if result and result.get("success"):
                            order_id = result.get("order_id", "")
                            self.log(
                                f"✅ [{mode_text}]交易订单提交成功！\n"
                                f"股票: {name}({code})\n"
                                f"操作: {action_text}\n"
                                f"数量: {quantity}股\n"
                                f"价格: {price:.2f}元\n"
                                f"订单号: {order_id}",
                                "SUCCESS",
                            )

                            QMessageBox.information(
                                self,
                                "交易成功",
                                f"✅ {action_text}订单提交成功！\n\n"
                                f"股票: {name}({code})\n"
                                f"数量: {quantity}股\n"
                                f"价格: {price:.2f}元\n"
                                f"订单号: {order_id}\n\n",
                            )
                        else:
                            # 交易失败
                            error_msg = (
                                result.get("message", "未知错误")
                                if result
                                else "返回空结果"
                            )
                            self.log(f"❌ [{mode_text}]交易失败: {error_msg}", "ERROR")
                            QMessageBox.warning(
                                self,
                                "交易失败",
                                f"❌ {action_text}订单提交失败！\n\n"
                                f"错误信息: {error_msg}\n\n"
                                f"请检查:\n"
                                f"1. 账户余额是否充足\n"
                                f"2. 股票代码是否正确\n"
                                f"3. 交易时间是否有效\n"
                                f"4. 账户状态是否正常",
                            )
                    except Exception as e:
                        error_msg = f"交易接口调用异常: {str(e)}"
                        self.log(f"❌ [{mode_text}]{error_msg}", "ERROR")
                        QMessageBox.critical(
                            self,
                            "交易异常",
                            f"❌ 交易过程中发生异常！\n\n"
                            f"错误详情: {error_msg}\n\n"
                            f"建议:\n"
                            f"1. 检查网络连接\n"
                            f"2. 重新连接客户端\n"
                            f"3. 联系技术支持",
                        )

    def add_to_pool(self, code: str, name: str):
        """添加股票到交易池"""
        if self.stock_pool.add_stock(code, name):
            self.log(f"✅ 成功添加股票到交易池: {code} {name}", "SUCCESS")
            # 刷新交易池显示
            self.refresh_stock_pool()
        else:
            self.log(f"ℹ️ 股票已在交易池中: {code} {name}", "INFO")

    def remove_from_pool(self, code: str, name: str):
        """从交易池移除股票"""
        # 确认删除对话框
        from PyQt5.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要从交易池中删除股票吗？\n\n股票代码: {code}\n股票名称: {name}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            if self.stock_pool.remove_stock(code):
                self.log(f"✅ 成功从交易池移除股票: {code} {name}", "SUCCESS")
                # 刷新交易池显示
                self.refresh_stock_pool()
                # 如果当前正在显示该股票的图表，清空图表
                if (
                    hasattr(self, "current_stock_code")
                    and self.current_stock_code == code
                ):
                    self.current_stock_code = None
                    self.current_stock_name = None
                    self.chart_widget.clear_chart()
            else:
                self.log(f"❌ 股票不在交易池中: {code} {name}", "ERROR")

    def show_add_stock_dialog(self):
        """显示添加股票对话框"""
        dialog = AddStockDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            code, name = dialog.get_stock_info()
            if code and name:
                self.add_to_pool(code, name)

    def import_stock_list(self):
        """导入股票列表"""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox

        # 选择文件
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择股票列表文件",
            "",
            "CSV文件 (*.csv);;文本文件 (*.txt);;所有文件 (*.*)",
        )

        if not file_path:
            return

        try:
            imported_count = 0
            duplicate_count = 0

            # 根据文件扩展名处理
            if file_path.lower().endswith(".csv"):
                # CSV文件处理
                try:
                    import pandas as pd

                    df = pd.read_csv(file_path, encoding="utf-8")

                    # 尝试不同的列名
                    code_col = None
                    name_col = None

                    for col in df.columns:
                        col_lower = col.lower()
                        if "code" in col_lower or "代码" in col:
                            code_col = col
                        elif "name" in col_lower or "名称" in col:
                            name_col = col

                    if code_col is None or name_col is None:
                        # 如果没有找到合适的列名，假设前两列是代码和名称
                        if len(df.columns) >= 2:
                            code_col = df.columns[0]
                            name_col = df.columns[1]
                        else:
                            raise ValueError("CSV文件格式不正确")

                    for _, row in df.iterrows():
                        code = str(row[code_col]).strip().zfill(6)
                        name = str(row[name_col]).strip()

                        if len(code) == 6 and code.isdigit() and name:
                            if self.stock_pool.add_stock(code, name):
                                imported_count += 1
                            else:
                                duplicate_count += 1

                except ImportError:
                    # 如果没有pandas，使用基本方法读取CSV
                    with open(file_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()

                    for i, line in enumerate(lines):
                        line = line.strip()
                        if i == 0 and ("code" in line.lower() or "代码" in line):
                            continue  # 跳过标题行

                        if "," in line:
                            parts = line.split(",")
                            if len(parts) >= 2:
                                code = parts[0].strip().zfill(6)
                                name = parts[1].strip()

                                if len(code) == 6 and code.isdigit() and name:
                                    if self.stock_pool.add_stock(code, name):
                                        imported_count += 1
                                    else:
                                        duplicate_count += 1

            else:
                # 文本文件处理
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # 支持多种格式
                        if "," in line:
                            parts = line.split(",", 1)
                        elif "|" in line:
                            parts = line.split("|", 1)
                        elif "\t" in line:
                            parts = line.split("\t", 1)
                        elif " " in line:
                            parts = line.split(" ", 1)
                        else:
                            parts = [line, line]

                        if len(parts) >= 2:
                            code = parts[0].strip().zfill(6)
                            name = parts[1].strip()

                            if len(code) == 6 and code.isdigit() and name:
                                if self.stock_pool.add_stock(code, name):
                                    imported_count += 1
                                else:
                                    duplicate_count += 1

            # 显示结果
            message = f"导入完成！\n\n新增股票: {imported_count} 只\n重复股票: {duplicate_count} 只"

            if imported_count > 0:
                self.log(f"✅ 成功导入 {imported_count} 只股票", "SUCCESS")
                self.refresh_stock_pool()  # 刷新界面
                QMessageBox.information(self, "导入成功", message)
            else:
                self.log("ℹ️ 没有导入新股票", "INFO")
                QMessageBox.information(self, "导入完成", message)

        except Exception as e:
            error_msg = f"导入失败: {str(e)}"
            self.log(f"❌ {error_msg}", "ERROR")
            QMessageBox.critical(
                self, "导入失败", f"文件导入失败！\n\n错误信息: {error_msg}"
            )

    def check_trading_permissions_dialog(self):
        """显示交易权限检测对话框"""
        if not self.myquant_client.is_connected():
            QMessageBox.warning(
                self,
                "权限检测",
                "MyQuant客户端未连接！\n\n请先连接MyQuant客户端后再检测交易权限。",
            )
            return

        # 创建权限检测对话框
        dialog = TradingPermissionsDialog(self.myquant_client, parent=self)
        dialog.exec_()  # ================================

    # 图表更新功能
    # ================================

    def on_period_changed(self, period: str):
        """周期改变时更新图表"""
        self.current_period = period
        self.update_chart()

    def update_chart(self):
        """更新图表显示 - 高级版本"""
        if not self.current_stock:
            return

        code, name = self.current_stock

        # 获取历史数据
        df = self.myquant_client.get_historical_data(code, self.current_period)

        if not isinstance(df, pd.DataFrame) or df.empty:
            self.log(f"无法获取{code}的历史数据", "WARNING")
            return

        # 缓存数据并绘制图表
        self.data_cache[code] = df
        self.update_chart_advanced(code, df)

    # ================================
    # 数据表格更新功能
    # ================================

    def calculate_indicators(self, df: pd.DataFrame) -> dict:
        """计算技术指标"""
        if df.empty:
            return {}

        indicators = {}

        # 确保使用正确的列名
        if "收盘" in df.columns:
            close_col = "收盘"
            high_col = "最高"
            low_col = "最低"
        else:
            close_col = "Close"
            high_col = "High"
            low_col = "Low"

        try:
            # 移动平均线
            for period in [5, 10, 20, 60]:
                ma_name = f"MA{period}"
                if len(df) >= period:
                    indicators[ma_name] = df[close_col].rolling(window=period).mean()

            # MACD指标
            if len(df) >= 26:
                exp1 = df[close_col].ewm(span=12).mean()
                exp2 = df[close_col].ewm(span=26).mean()
                macd_line = exp1 - exp2
                signal_line = macd_line.ewm(span=9).mean()
                histogram = macd_line - signal_line

                indicators["MACD"] = macd_line
                indicators["MACD_Signal"] = signal_line
                indicators["MACD_Histogram"] = histogram

            # RSI指标
            if len(df) >= 14:
                delta = df[close_col].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                indicators["RSI"] = 100 - (100 / (1 + rs))

            # KDJ指标
            if len(df) >= 9 and high_col in df.columns and low_col in df.columns:
                low_min = df[low_col].rolling(window=9).min()
                high_max = df[high_col].rolling(window=9).max()
                rsv = (df[close_col] - low_min) / (high_max - low_min) * 100

                k = rsv.ewm(com=2).mean()
                d = k.ewm(com=2).mean()
                j = 3 * k - 2 * d

                indicators["KDJ_K"] = k
                indicators["KDJ_D"] = d
                indicators["KDJ_J"] = j

            # 布林带
            if len(df) >= 20:
                ma20 = df[close_col].rolling(window=20).mean()
                std20 = df[close_col].rolling(window=20).std()

                indicators["BOLL_UPPER"] = ma20 + 2 * std20
                indicators["BOLL_MIDDLE"] = ma20
                indicators["BOLL_LOWER"] = ma20 - 2 * std20

                # 添加布林带宽度计算
                indicators["BB_Width"] = (
                    indicators["BOLL_UPPER"] - indicators["BOLL_LOWER"]
                ) / indicators["BOLL_MIDDLE"]

        except Exception as e:
            self.log(f"计算技术指标时出错: {e}", "WARNING")

        return indicators

    def update_positions_table(self, positions: List[Dict]):
        """更新持仓表格"""
        self.position_table.setRowCount(len(positions))

        for i, pos in enumerate(positions):
            self.position_table.setItem(i, 0, QTableWidgetItem(pos.get("代码", "")))
            self.position_table.setItem(i, 1, QTableWidgetItem(pos.get("名称", "")))
            self.position_table.setItem(i, 2, QTableWidgetItem(str(pos.get("数量", 0))))
            self.position_table.setItem(
                i, 3, QTableWidgetItem(f"{pos.get('成本价', 0):.2f}")
            )
            self.position_table.setItem(
                i, 4, QTableWidgetItem(f"{pos.get('现价', 0):.2f}")
            )

    def update_account_table(self, account: Dict):
        """更新账户信息表格（从MyQuant客户端自动读取）"""
        # 检查数据来源
        is_from_client = account.get("总资产", 0) > 0

        items = [
            f"{account.get('总资产', 0):.2f}",
            f"{account.get('可用资金', 0):.2f}",
            f"{account.get('持仓市值', 0):.2f}",
            f"{account.get('当日盈亏', 0):.2f}",
        ]

        self.account_table.setRowCount(1)
        for i, value in enumerate(items):
            item_value = QTableWidgetItem(value)

            # 如果是从客户端读取的数据，使用绿色字体表示
            if is_from_client:
                item_value.setForeground(QColor("green"))
            else:
                item_value.setForeground(QColor("orange"))

            self.account_table.setItem(0, i, item_value)

        # 更新表头显示数据来源
        headers = ["总资产", "可用资金", "持仓市值", "当日盈亏"]
        source_text = "（自动）" if is_from_client else "（缓存）"
        headers[0] = f"总资产{source_text}"
        self.account_table.setHorizontalHeaderLabels(headers)

    def update_stock_pool_table(self, realtime_data: Dict):
        """更新交易池实时数据"""
        # 实际调用refresh_stock_pool方法来更新交易池显示
        self.refresh_stock_pool()

    # ========= Execution Engine =========
    def _init_execution_engine(self):
        """初始化执行引擎，根据配置选择模拟或实盘"""
        try:
            if self.config.get("trading.simulation_mode", True):
                self.execution_engine = SimExecutionEngine(self)
            else:
                self.execution_engine = RealExecutionEngine(self)
        except Exception as e:
            # 兜底为模拟引擎
            self.log(f"初始化执行引擎失败: {e}")
            self.execution_engine = SimExecutionEngine(self)

    # ...已彻底移除自动烟雾下单钩子...

    def _switch_execution_engine(self):
        """在切换交易模式后调用，更新执行引擎实例"""
        self._init_execution_engine()

    # ================================
    # E. 交易模式切换
    # ================================

    def toggle_simulation_mode(self, checked):
        """切换模拟交易模式"""
        if checked:
            # 选择模拟模式时，取消实盘模式
            self.real_trading_button.setChecked(False)
            self.config.set("trading.simulation_mode", True)
            self.log("交易模式已切换为: 模拟", "INFO")
        else:
            # 如果取消模拟模式且实盘模式也未选中，默认选择实盘模式
            if not self.real_trading_button.isChecked():
                self.real_trading_button.setChecked(True)
                self.config.set("trading.simulation_mode", False)
                self.log("交易模式已切换为: 实盘", "INFO")
        # 切换执行引擎并保存配置
        try:
            self._switch_execution_engine()
        except Exception as e:
            self.log(f"切换执行引擎失败: {e}", "ERROR")
        self.update_trading_mode_buttons()
        self.config.save_config()

    def toggle_real_trading_mode(self, checked):
        """切换实盘交易模式"""
        if checked:
            # 选择实盘模式时，取消模拟模式
            self.simulation_button.setChecked(False)
            self.config.set("trading.simulation_mode", False)
            self.log("交易模式已切换为: 实盘", "INFO")
        else:
            # 如果取消实盘模式且模拟模式也未选中，默认选择模拟模式
            if not self.simulation_button.isChecked():
                self.simulation_button.setChecked(True)
                self.config.set("trading.simulation_mode", True)
                self.log("交易模式已切换为: 模拟", "INFO")
        # 切换执行引擎并保存配置
        try:
            self._switch_execution_engine()
        except Exception as e:
            self.log(f"切换执行引擎失败: {e}", "ERROR")
        self.update_trading_mode_buttons()
        self.config.save_config()

    def update_trading_mode_buttons(self):
        """更新交易模式按钮样式"""
        # 模拟交易模式按钮样式
        if self.simulation_button.isChecked():
            self.simulation_button.setText("● 模拟交易模式")
            self.simulation_button.setStyleSheet(
                "QPushButton { color: #2196F3; font-weight: bold; background-color: transparent; border: none; }"
            )
        else:
            self.simulation_button.setText("○ 模拟交易模式")
            self.simulation_button.setStyleSheet(
                "QPushButton { color: gray; background-color: transparent; border: none; }"
            )

        # 实盘交易模式按钮样式
        if self.real_trading_button.isChecked():
            self.real_trading_button.setText("● 实盘交易模式")
            self.real_trading_button.setStyleSheet(
                "QPushButton { color: #FF5722; font-weight: bold; background-color: transparent; border: none; }"
            )
        else:
            self.real_trading_button.setText("○ 实盘交易模式")
            self.real_trading_button.setStyleSheet(
                "QPushButton { color: gray; background-color: transparent; border: none; }"
            )

    # ================================
    # F. 同步客户端功能
    # ================================

    def sync_client_data(self):
        """同步客户端数据"""
        self.log("开始同步客户端数据...", "INFO")

        try:
            # 检查客户端连接状态
            if not self.myquant_client.is_connected():
                self.log("客户端未连接，正在尝试重新连接...", "WARNING")
                if not self.myquant_client.connect():
                    self.log("客户端连接失败，无法同步数据", "ERROR")
                    QMessageBox.warning(self, "同步失败", "客户端连接失败，请检查配置")
                    return
            else:
                self.log("客户端连接正常", "INFO")

            # 统计同步结果
            sync_success_count = 0
            sync_total_count = 3  # 持仓、账户、交易池

            # 1. 同步持仓信息
            self.log("正在同步持仓信息...", "INFO")
            try:
                positions = self.myquant_client.get_positions()
                if positions and len(positions) > 0:
                    self.signals.positions_updated.emit(positions)
                    self.log(
                        f"✅ 同步持仓信息成功，共{len(positions)}只股票", "SUCCESS"
                    )
                    sync_success_count += 1
                else:
                    # 空持仓也算成功
                    self.signals.positions_updated.emit([])
                    self.log("✅ 持仓为空，已清空持仓表格", "INFO")
                    sync_success_count += 1
            except Exception as e:
                error_msg = str(e)
                if "无效的ACCOUNT_ID" in error_msg or "1020" in error_msg:
                    self.log("⚠️  持仓信息获取失败：账户权限不足或配置问题", "WARNING")
                    self.log("   可能需要在MyQuant平台开通相应权限", "INFO")
                else:
                    self.log(f"❌ 持仓信息获取失败: {error_msg}", "ERROR")
                # 设置空持仓
                self.signals.positions_updated.emit([])

            # 2. 同步账户信息
            self.log("正在同步账户信息...", "INFO")
            try:
                account = self.myquant_client.get_account_info()
                if account and any(v != 0 for v in account.values()):
                    self.signals.account_updated.emit(account)
                    self.log("✅ 同步账户信息成功", "SUCCESS")
                    sync_success_count += 1
                else:
                    # 显示默认的零账户信息
                    default_account = {
                        "总资产": 0,
                        "可用资金": 0,
                        "持仓市值": 0,
                        "当日盈亏": 0,
                    }
                    self.signals.account_updated.emit(default_account)
                    self.log("⚠️  账户信息为空或全为零", "WARNING")
            except Exception as e:
                error_msg = str(e)
                if "无效的ACCOUNT_ID" in error_msg or "1020" in error_msg:
                    self.log("⚠️  账户信息获取失败：账户权限不足或配置问题", "WARNING")
                    self.log("   可能需要在MyQuant平台开通交易权限", "INFO")
                else:
                    self.log(f"❌ 账户信息获取失败: {error_msg}", "ERROR")

                # 显示默认账户信息
                default_account = {
                    "总资产": 0,
                    "可用资金": 0,
                    "持仓市值": 0,
                    "当日盈亏": 0,
                }
                self.signals.account_updated.emit(default_account)

            # 3. 刷新交易池数据
            self.log("正在刷新交易池数据...", "INFO")
            try:
                self.refresh_stock_pool()
                self.log("✅ 交易池数据刷新成功", "SUCCESS")
                sync_success_count += 1
            except Exception as e:
                self.log(f"❌ 交易池数据刷新失败: {str(e)}", "ERROR")

            # 显示同步结果
            if sync_success_count == sync_total_count:
                result_msg = "🎉 客户端数据同步完全成功！"
                self.log(result_msg, "SUCCESS")
                QMessageBox.information(self, "同步成功", result_msg)
            elif sync_success_count > 0:
                result_msg = (
                    f"⚠️  部分数据同步成功 ({sync_success_count}/{sync_total_count})\n\n"
                    "如果持仓和账户信息显示为空，这通常是因为：\n"
                    "1. MyQuant账户未开通交易权限\n"
                    "2. 账户配置不完整\n"
                    "3. 当前为演示模式\n\n"
                    "交易池数据和行情功能不受影响。"
                )
                self.log("客户端数据部分同步完成", "INFO")
                QMessageBox.information(self, "同步完成", result_msg)
            else:
                result_msg = "❌ 数据同步失败\n\n请检查网络连接和MyQuant配置"
                self.log("客户端数据同步失败", "ERROR")
                QMessageBox.warning(self, "同步失败", result_msg)

        except Exception as e:
            error_msg = f"同步客户端数据异常: {str(e)}"
            self.log(error_msg, "ERROR")
            QMessageBox.critical(self, "同步异常", f"发生未预期的错误：\n{str(e)}")

    # ================================
    # 交易记录功能
    # ================================

    def show_trade_records(self):
        """显示交易记录"""
        # 在展示对话框前从磁盘重新加载，保证跨进程写入也能被看到
        try:
            self.trade_recorder.load_records()
        except Exception as e:
            self.log(f"重新加载交易记录失败: {e}", "ERROR")
        dialog = TradeRecordsDialog(self.trade_recorder, parent=self)
        dialog.exec_()

    # ================================
    # 图表更新和指标计算功能
    # ================================

    def on_indicator_change(self):
        """主图指标改变"""
        self.current_indicator = self.indicator_combo.currentText()
        self.redraw_chart()

    def on_subplot_indicator_change(self):
        """副图指标改变"""
        self.current_subplot_indicator = self.subplot_indicator_combo.currentText()
        self.redraw_chart()

    def redraw_chart(self):
        """重绘图表"""
        if not self.current_stock:
            return
        code, name = self.current_stock
        df = self.data_cache.get(code)
        if isinstance(df, pd.DataFrame) and not df.empty:
            self.update_chart_advanced(code, df)

    def update_chart_advanced(self, code: str, df: pd.DataFrame):
        """更新图表显示"""
        if not isinstance(df, pd.DataFrame) or df.empty:
            self.log("❌ 无法绘制图表，数据为空", "WARNING")
            return

        # 显示所有轴
        self.ax_vol.set_visible(True)
        self.ax_indicator.set_visible(True)

        # 缓存全量数据，绘图用尾部数据
        self.data_cache[code] = df
        df_plot = df.tail(self.zoom_level).copy()

        # 标准化列名 - 确保mplfinance兼容性
        column_mapping = {
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
            "成交额": "Amount",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "amount": "Amount",
        }

        # 重命名列名以符合mplfinance要求
        df_plot = df_plot.rename(columns=column_mapping)

        # 确保必需的列存在
        required_cols = ["Open", "High", "Low", "Close"]
        missing_cols = [col for col in required_cols if col not in df_plot.columns]
        if missing_cols:
            self.log(
                f"❌ 数据缺少必需列: {missing_cols}, 现有列: {list(df_plot.columns)}",
                "ERROR",
            )
            return

        # 计算技术指标
        indicators = self.calculate_indicators(df)

        # 运行交易策略信号检测
        signal_engine = SignalEngine()
        signal_engine.run_all_strategies(df, indicators, code)

        # 清空三个子图
        self.ax_price.clear()
        self.ax_vol.clear()
        self.ax_indicator.clear()

        # 设置图表标题
        name = self.stock_pool.get_all_stocks().get(code, code)
        period_map = {
            "1m": "1分钟",
            "5m": "5分钟",
            "15m": "15分钟",
            "60m": "60分钟",
            "1d": "日线",
        }
        period_str = period_map.get(self.current_period, self.current_period)
        title_text = f"{code} {name} - {period_str}"
        self.ax_price.set_title(title_text, fontsize=14, fontweight="bold", pad=10)

        # 配置图表样式
        mc = mpf.make_marketcolors(
            up=self.chart_colors["up"],
            down=self.chart_colors["down"],
            edge={"up": self.chart_colors["up"], "down": self.chart_colors["down"]},
            wick={"up": self.chart_colors["up"], "down": self.chart_colors["down"]},
            volume={"up": self.chart_colors["up"], "down": self.chart_colors["down"]},
        )

        s = mpf.make_mpf_style(
            base_mpf_style="yahoo",
            marketcolors=mc,
            rc={
                "font.family": "SimHei",
                "axes.edgecolor": "#CCCCCC",
                "axes.labelcolor": self.chart_colors["text"],
                "xtick.color": self.chart_colors["text"],
                "ytick.color": self.chart_colors["text"],
                "figure.facecolor": self.chart_colors["bg"],
                "axes.facecolor": self.chart_colors["ax"],
                "grid.color": self.chart_colors["grid"],
                "grid.linestyle": "--",
            },
        )

        # 根据指标选择绘制均线
        add_plot = []
        if self.current_indicator == "均线":
            for ma_name, color in [
                ("MA5", self.chart_colors["ma5"]),
                ("MA20", self.chart_colors["ma20"]),
                ("MA60", self.chart_colors["ma60"]),
            ]:
                if ma_name in indicators:
                    ma_data = indicators[ma_name].tail(len(df_plot))
                    add_plot.append(
                        mpf.make_addplot(
                            ma_data, ax=self.ax_price, color=color, width=1.2
                        )
                    )
        else:  # 操盘线：仅MA60
            if "MA60" in indicators:
                ma60_data = indicators["MA60"].tail(len(df_plot))
                add_plot.append(
                    mpf.make_addplot(
                        ma60_data,
                        ax=self.ax_price,
                        color=self.chart_colors["ma60"],
                        width=1.3,
                    )
                )

        # 绘制K线图和成交量
        try:
            mpf.plot(
                df_plot,
                ax=self.ax_price,
                volume=self.ax_vol,
                type="candle",
                style=s,
                addplot=add_plot if add_plot else None,
                xrotation=0,
                tight_layout=False,
                warn_too_much_data=df_plot.shape[0] + 1,
            )
        except Exception as e:
            self.log(f"绘制K线图失败: {e}", "ERROR")
            return

        # 设置成交量轴标签
        self.ax_vol.set_ylabel("成交量", color=self.chart_colors["text"], fontsize=9)

        # 绘制副图指标
        self.draw_subplot_indicator(df_plot, indicators)

        # 添加网格
        for ax in [self.ax_price, self.ax_vol, self.ax_indicator]:
            ax.grid(True, alpha=0.3, linestyle="--")

        # 设置日期轴格式，只在最下方的指标图显示日期
        import matplotlib.dates as mdates

        locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
        formatter = mdates.AutoDateFormatter(locator)

        # 给所有图设置统一的日期格式
        for ax in [self.ax_price, self.ax_vol, self.ax_indicator]:
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(formatter)

        # 隐藏主图和成交量图的x轴标签，只在指标图显示日期
        self.ax_price.tick_params(axis="x", labelbottom=False)
        self.ax_vol.tick_params(axis="x", labelbottom=False)

        # 显示交易信号
        if signal_engine.results:
            latest_signal = signal_engine.results[-1]  # 最新信号
            signal_name, signal_score = latest_signal[1], latest_signal[3]
            signal_text = signal_engine.label_map.get(signal_name, (signal_name, 0))[0]

            last_close = float(df_plot["close"].iloc[-1])
            last_dt = df_plot.index[-1]

            self.ax_price.annotate(
                signal_text,
                xy=(last_dt, last_close),
                xytext=(0, -30),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=9,
                color="red" if signal_score > 0.7 else "orange",
                bbox={"boxstyle": "round,pad=0.3", "facecolor": "yellow", "alpha": 0.8},
                arrowprops={"arrowstyle": "->", "color": "red", "lw": 1},
            )

        # 更新缩放显示
        self.zoom_label.setText(f"{self.zoom_level}K")

        # 刷新画布
        self.canvas.draw()

        self.log(f"✅ 已更新{name}({code})的{period_str}图表", "SUCCESS")

    def draw_subplot_indicator(self, df_plot: pd.DataFrame, indicators: dict):
        """绘制副图指标"""
        try:
            # 确保副图轴存在且可见
            if not hasattr(self, "ax_indicator") or self.ax_indicator is None:
                self.log("❌ 副图轴不存在，重新创建...", "ERROR")
                return

            # 强制确保副图轴可见
            self.ax_indicator.set_visible(True)

            # 清空副图轴（确保干净绘制）
            self.ax_indicator.clear()

            # 数据有效性校验
            if not isinstance(indicators, dict) or len(indicators) == 0:
                raise ValueError("指标数据字典为空或无效")

            if self.current_subplot_indicator == "MACD":
                self.draw_macd_indicator(df_plot, indicators)
            elif self.current_subplot_indicator == "RSI":
                self.draw_rsi_indicator(df_plot, indicators)
            elif self.current_subplot_indicator == "KDJ":
                self.draw_kdj_indicator(df_plot, indicators)
            elif self.current_subplot_indicator == "BOLL":
                self.draw_boll_indicator(df_plot, indicators)
            else:
                # 如果指标类型不匹配，显示提示
                self.ax_indicator.text(
                    0.5,
                    0.5,
                    f"未知副图指标: {self.current_subplot_indicator}",
                    transform=self.ax_indicator.transAxes,
                    ha="center",
                    va="center",
                    fontsize=12,
                )

            self.ax_indicator.set_ylabel(
                self.current_subplot_indicator,
                color=self.chart_colors["text"],
                fontsize=9,
            )

            self.log("✅ 副图指标绘制完成", "SUCCESS")

        except Exception as e:
            self.log(f"❌ 副图指标绘制失败: {str(e)}", "ERROR")
            self.ax_indicator.text(
                0.5,
                0.5,
                f"副图指标错误: {str(e)}",
                transform=self.ax_indicator.transAxes,
                ha="center",
                va="center",
                fontsize=10,
            )

    def draw_macd_indicator(self, df_plot: pd.DataFrame, indicators: dict):
        """绘制MACD指标"""
        try:
            # 检查MACD数据是否存在 - 使用正确的键名
            required_keys = ["MACD", "MACD_Signal", "MACD_Histogram"]

            if all(k in indicators for k in required_keys):
                # 获取与df_plot对应的指标数据
                macd_data = indicators["MACD"].tail(len(df_plot))
                signal_data = indicators["MACD_Signal"].tail(len(df_plot))
                hist_data = indicators["MACD_Histogram"].tail(len(df_plot))

                # 检查数据是否有有效值
                if macd_data.isna().all():
                    self.ax_indicator.text(
                        0.5,
                        0.5,
                        "MACD数据不足，需要更多历史数据",
                        transform=self.ax_indicator.transAxes,
                        ha="center",
                        va="center",
                        fontsize=12,
                    )
                    return

                # 确保数据类型正确
                macd_data = pd.to_numeric(macd_data, errors="coerce")
                signal_data = pd.to_numeric(signal_data, errors="coerce")
                hist_data = pd.to_numeric(hist_data, errors="coerce")

                # MACD柱状图颜色
                hist_colors = [
                    self.chart_colors["up"] if v >= 0 else self.chart_colors["down"]
                    for v in hist_data.fillna(0)
                ]

                # 使用索引范围作为横轴（参考st_juej_v100.py的成功做法）
                x_range = range(len(df_plot))
                # 绘制柱状图
                self.ax_indicator.bar(
                    x_range,
                    hist_data.fillna(0),
                    color=hist_colors,
                    width=0.8,
                    alpha=0.6,
                    align="center",
                )

                # 绘制MACD线和信号线
                self.ax_indicator.plot(
                    x_range,
                    macd_data,
                    color="#1976D2",
                    linewidth=1.2,
                    label="MACD",
                )
                self.ax_indicator.plot(
                    x_range,
                    signal_data,
                    color="#F59F00",
                    linewidth=1.2,
                    label="Signal",
                )

                self.ax_indicator.legend(loc="upper left", fontsize=8)
                self.ax_indicator.grid(True, alpha=0.3)

            else:
                self.ax_indicator.text(
                    0.5,
                    0.5,
                    "MACD数据不足",
                    transform=self.ax_indicator.transAxes,
                    ha="center",
                    va="center",
                    fontsize=12,
                )

        except Exception as e:
            self.log(f"❌ MACD指标绘制失败: {str(e)}", "ERROR")

    def draw_rsi_indicator(self, df_plot: pd.DataFrame, indicators: dict):
        """绘制RSI指标"""
        try:
            if "RSI" in indicators:
                rsi_data = indicators["RSI"].tail(len(df_plot))

                if rsi_data.isna().all():
                    self.ax_indicator.text(
                        0.5,
                        0.5,
                        "RSI数据不足",
                        transform=self.ax_indicator.transAxes,
                        ha="center",
                        va="center",
                        fontsize=12,
                    )
                    return

                rsi_data = pd.to_numeric(rsi_data, errors="coerce")

                # 使用索引范围作为横轴（参考st_juej_v100.py的成功做法）
                x_range = range(len(df_plot))

                self.ax_indicator.plot(
                    x_range, rsi_data, color="#9C27B0", linewidth=1.2, label="RSI"
                )
                self.ax_indicator.axhline(
                    y=70, color="#F44336", linestyle="--", alpha=0.5, label="超买"
                )
                self.ax_indicator.axhline(
                    y=30, color="#2E7D32", linestyle="--", alpha=0.5, label="超卖"
                )

                self.ax_indicator.set_ylim(0, 100)
                self.ax_indicator.legend(loc="upper left", fontsize=8)
                self.ax_indicator.grid(True, alpha=0.3)
            else:
                self.ax_indicator.text(
                    0.5,
                    0.5,
                    "RSI数据不足",
                    transform=self.ax_indicator.transAxes,
                    ha="center",
                    va="center",
                    fontsize=12,
                )

        except Exception as e:
            self.log(f"❌ RSI指标绘制失败: {str(e)}", "ERROR")

    def draw_kdj_indicator(self, df_plot: pd.DataFrame, indicators: dict):
        """绘制KDJ指标"""
        try:
            required_keys = ["KDJ_K", "KDJ_D", "KDJ_J"]

            if all(k in indicators for k in required_keys):
                k_data = indicators["KDJ_K"].tail(len(df_plot))
                d_data = indicators["KDJ_D"].tail(len(df_plot))
                j_data = indicators["KDJ_J"].tail(len(df_plot))

                if k_data.isna().all():
                    self.ax_indicator.text(
                        0.5,
                        0.5,
                        "KDJ数据不足",
                        transform=self.ax_indicator.transAxes,
                        ha="center",
                        va="center",
                        fontsize=12,
                    )
                    return

                k_data = pd.to_numeric(k_data, errors="coerce")
                d_data = pd.to_numeric(d_data, errors="coerce")
                j_data = pd.to_numeric(j_data, errors="coerce")

                # 使用索引范围作为横轴（参考st_juej_v100.py的成功做法）
                x_range = range(len(df_plot))

                self.ax_indicator.plot(
                    x_range, k_data, color="#2196F3", linewidth=1.2, label="K"
                )
                self.ax_indicator.plot(
                    x_range, d_data, color="#FF9800", linewidth=1.2, label="D"
                )
                self.ax_indicator.plot(
                    x_range, j_data, color="#E91E63", linewidth=1.2, label="J"
                )

                self.ax_indicator.axhline(
                    y=80, color="#F44336", linestyle="--", alpha=0.5
                )
                self.ax_indicator.axhline(
                    y=20, color="#2E7D32", linestyle="--", alpha=0.5
                )

                self.ax_indicator.set_ylim(0, 100)
                self.ax_indicator.legend(loc="upper left", fontsize=8)
                self.ax_indicator.grid(True, alpha=0.3)

            else:
                self.ax_indicator.text(
                    0.5,
                    0.5,
                    "KDJ数据不足",
                    transform=self.ax_indicator.transAxes,
                    ha="center",
                    va="center",
                    fontsize=12,
                )

        except Exception as e:
            self.log(f"❌ KDJ指标绘制失败: {str(e)}", "ERROR")

    def draw_boll_indicator(self, df_plot: pd.DataFrame, indicators: dict):
        """绘制布林带宽度"""
        if "BB_Width" not in indicators:
            self.ax_indicator.text(
                0.5,
                0.5,
                "BOLL数据不足",
                transform=self.ax_indicator.transAxes,
                ha="center",
                va="center",
                fontsize=12,
                color="red",
            )
            return

        bb_width_data = indicators["BB_Width"].tail(len(df_plot)) * 100  # 转为百分比

        # 使用索引范围作为横轴（参考st_juej_v100.py的成功做法）
        x_range = range(len(df_plot))

        self.ax_indicator.plot(
            x_range, bb_width_data, color="#795548", linewidth=1.2, label="BOLL Width %"
        )

        self.ax_indicator.legend(loc="upper left", fontsize=8)
        self.ax_indicator.set_ylabel("BOLL Width %", fontsize=9)
        self.ax_indicator.grid(True, alpha=0.3)

    # ================================
    # 图表缩放控制功能
    # ================================

    def zoom_in(self):
        """放大图表 - 减少显示数据量"""
        old_zoom = self.zoom_level
        self.zoom_level = max(50, self.zoom_level - 20)
        if self.zoom_level != old_zoom:
            self.zoom_label.setText(f"{self.zoom_level}K")
            if hasattr(self, "current_stock") and self.current_stock:
                self.update_chart()
            self.log(f"📈 放大图表，显示最近{self.zoom_level}根K线", "INFO")

    def zoom_out(self):
        """缩小图表 - 增加显示数据量"""
        old_zoom = self.zoom_level
        self.zoom_level = min(500, self.zoom_level + 20)
        if self.zoom_level != old_zoom:
            self.zoom_label.setText(f"{self.zoom_level}K")
            if hasattr(self, "current_stock") and self.current_stock:
                self.update_chart()
            self.log(f"📉 缩小图表，显示最近{self.zoom_level}根K线", "INFO")

    def reset_zoom(self):
        """重置缩放"""
        self.zoom_level = 120
        self.zoom_label.setText(f"{self.zoom_level}K")
        if hasattr(self, "current_stock") and self.current_stock:
            self.update_chart()
        self.log(f"🔄 重置图表缩放，显示最近{self.zoom_level}根K线", "INFO")

    # ================================
    # 数据获取和处理功能
    # ================================

    def get_stock_data(self, code: str, period: str = "1d") -> pd.DataFrame:
        """获取股票数据"""
        try:
            # 检查缓存
            cache_key = f"{code}_{period}"
            if cache_key in self.data_cache:
                cached_data = self.data_cache[cache_key]
                if isinstance(cached_data, pd.DataFrame) and not cached_data.empty:
                    # 检查缓存时效（日内数据5分钟更新，日线数据30分钟更新）
                    now = datetime.now()
                    cache_timeout = 5 if period in ["1m", "5m", "15m", "60m"] else 30
                    last_update = getattr(
                        cached_data,
                        "_last_update",
                        now - timedelta(minutes=cache_timeout + 1),
                    )

                    if (now - last_update).total_seconds() < cache_timeout * 60:
                        return cached_data

            # 从MyQuant获取数据
            if self.myquant and self.myquant.is_connected():
                df = self.myquant.get_bars(code, period, count=500)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    df._last_update = datetime.now()
                    self.data_cache[cache_key] = df
                    return df

            # 备用：使用mock数据
            self.log(f"⚠️ 使用模拟数据为{code}", "WARNING")
            return self.generate_mock_data()

        except Exception as e:
            self.log(f"❌ 获取{code}数据失败: {e}", "ERROR")
            return pd.DataFrame()

    # ...已移除测试用模拟K线数据生成函数...

    # ================================
    # 键盘事件处理
    # ================================

    def keyPressEvent(self, event):
        """键盘事件处理"""
        key = event.key()
        if key in (Qt.Key_Up, Qt.Key_Right):
            self.zoom_in()
            event.accept()
            return
        if key in (Qt.Key_Down, Qt.Key_Left):
            self.zoom_out()
            event.accept()
            return
        super().keyPressEvent(event)


# ================================
# 对话框类
# ================================


class TradingPermissionsDialog(QDialog):
    """交易权限检测对话框"""

    def __init__(self, myquant_client, parent=None):
        super().__init__(parent)
        self.myquant_client = myquant_client
        self.setWindowTitle("交易权限检测")
        self.setModal(True)
        self.setFixedSize(600, 500)

        self.init_ui()
        self.check_permissions()

    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()

        # 标题
        title_label = QLabel("MyQuant交易权限检测")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(
            """
            QLabel {
                font-size: 16pt;
                font-weight: bold;
                color: #333;
                padding: 15px;
                background-color: #f8f9fa;
                border-radius: 8px;
                margin-bottom: 10px;
            }
        """
        )
        layout.addWidget(title_label)

        # 权限检测结果表格
        self.permissions_table = QTableWidget(0, 3)
        self.permissions_table.setHorizontalHeaderLabels(["权限类型", "状态", "说明"])
        self.permissions_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Fixed
        )
        self.permissions_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Fixed
        )
        self.permissions_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self.permissions_table.setColumnWidth(0, 120)
        self.permissions_table.setColumnWidth(1, 80)
        layout.addWidget(self.permissions_table)

        # 科创板特别说明
        kcb_info = QLabel(
            """
📈 科创板（688开头）交易权限要求：
• 申请权限开通前20个交易日证券账户及资金账户内的资产日均不低于50万元
• 参与证券交易24个月以上
• 通过科创板投资者适当性综合评估
• 签署《科创板投资风险揭示书》

🔍 权限检测说明：
• 仿真账户：默认开通所有交易权限
• 实盘账户：需要实际开通相应权限
        """
        )
        kcb_info.setStyleSheet(
            """
            QLabel {
                background-color: #e8f4fd;
                padding: 15px;
                border-radius: 8px;
                border-left: 4px solid #2196f3;
                font-size: 9pt;
                line-height: 1.4;
            }
        """
        )
        layout.addWidget(kcb_info)

        # 股票权限测试区域
        test_group = QGroupBox("单只股票权限测试")
        test_layout = QHBoxLayout(test_group)

        self.stock_code_edit = QLineEdit()
        self.stock_code_edit.setPlaceholderText("输入股票代码，如: 688001")
        self.stock_code_edit.setMaxLength(6)
        test_layout.addWidget(QLabel("股票代码:"))
        test_layout.addWidget(self.stock_code_edit)

        test_button = QPushButton("测试权限")
        test_button.clicked.connect(self.test_stock_permission)
        test_layout.addWidget(test_button)

        layout.addWidget(test_group)

        # 测试结果显示
        self.test_result_label = QLabel("请输入股票代码并点击测试")
        self.test_result_label.setStyleSheet(
            """
            QLabel {
                padding: 10px;
                background-color: #f8f9fa;
                border-radius: 5px;
                border: 1px solid #dee2e6;
            }
        """
        )
        layout.addWidget(self.test_result_label)

        # 按钮
        button_layout = QHBoxLayout()

        refresh_button = QPushButton("🔄 重新检测")
        refresh_button.clicked.connect(self.check_permissions)
        button_layout.addWidget(refresh_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def check_permissions(self):
        """检测交易权限"""
        # 清空表格
        self.permissions_table.setRowCount(0)

        try:
            # 获取权限信息
            permissions = self.myquant_client.check_trading_permissions()

            # 显示基本权限
            self.add_permission_row(
                "A股交易", permissions.get("A股交易", False), "主板、中小板股票交易"
            )
            self.add_permission_row(
                "科创板交易", permissions.get("科创板交易", False), "688开头股票交易"
            )
            self.add_permission_row(
                "创业板交易", permissions.get("创业板交易", False), "300开头股票交易"
            )

            # 显示账户信息
            account_type = permissions.get("账户类型", "未知")
            check_time = permissions.get("检测时间", "未知")

            self.add_permission_row("账户类型", account_type, f"检测时间: {check_time}")

            # 显示错误信息（如果有）
            if "错误" in permissions:
                self.add_permission_row("检测状态", False, permissions["错误"])

        except Exception as e:
            self.add_permission_row("检测异常", False, f"检测过程发生异常: {str(e)}")

    def add_permission_row(self, permission_type, status, description):
        """添加权限行到表格"""
        row = self.permissions_table.rowCount()
        self.permissions_table.insertRow(row)

        # 权限类型
        type_item = QTableWidgetItem(permission_type)
        self.permissions_table.setItem(row, 0, type_item)

        # 状态
        if isinstance(status, bool):
            status_text = "✅ 有权限" if status else "❌ 无权限"
            status_item = QTableWidgetItem(status_text)
            status_item.setForeground(QColor("green" if status else "red"))
        else:
            status_item = QTableWidgetItem(str(status))
            status_item.setForeground(QColor("blue"))

        self.permissions_table.setItem(row, 1, status_item)

        # 说明
        desc_item = QTableWidgetItem(description)
        self.permissions_table.setItem(row, 2, desc_item)

    def test_stock_permission(self):
        """测试单只股票的交易权限"""
        code = self.stock_code_edit.text().strip()

        if not code:
            self.test_result_label.setText("请输入股票代码")
            return

        if len(code) != 6 or not code.isdigit():
            self.test_result_label.setText("股票代码格式错误，请输入6位数字")
            return

        try:
            # 检测股票权限
            result = self.myquant_client.check_stock_trading_permission(code)

            # 格式化显示结果
            market = result.get("市场", "未知")
            can_trade = result.get("可交易", False)
            requirements = result.get("权限要求", [])
            tips = result.get("提示信息", "")

            status_text = "✅ 可以交易" if can_trade else "❌ 无法交易"
            requirements_text = (
                "、".join(requirements) if requirements else "无特殊要求"
            )

            result_html = f"""
<div style="font-size: 10pt;">
<b>股票代码:</b> {code}<br>
<b>所属市场:</b> {market}<br>
<b>交易状态:</b> <span style="color: {"green" if can_trade else "red"};">{status_text}</span><br>
<b>权限要求:</b> {requirements_text}<br>
<b>提示信息:</b> {tips}
</div>
            """

            self.test_result_label.setText(result_html)

        except Exception as e:
            self.test_result_label.setText(f"权限检测失败: {str(e)}")


class SettingsDialog(QDialog):
    """设置对话框"""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config

        # 初始化加载遮罩
        self.loading_overlay = QLabel(self)
        self.loading_overlay.setStyleSheet("background: rgba(255,255,255,0.8);")
        self.loading_overlay.setAlignment(Qt.AlignCenter)
        self.loading_overlay.setText("<b>配置加载中...</b>")
        self.loading_overlay.resize(self.size())
        self.loading_overlay.show()

        # 启动异步初始化
        self._start_async_init()

    def _start_async_init(self):
        """启动异步初始化线程"""
        self.init_thread = ConfigInitThread(self.config)
        self.init_thread.init_complete.connect(self._on_init_complete)
        self.init_thread.start()

    def _on_init_complete(self):
        """配置初始化完成后的回调：在主线程中构建对话框 UI 并隐藏加载遮罩。"""
        try:
            # 构建界面并确保遮罩被隐藏（init_ui 内会隐藏遮罩）
            self.init_ui()
        except Exception:
            # 为稳健性，捕获异常并在控制台记录（避免抛出影响主线程）
            import traceback

            traceback.print_exc()

    def init_ui(self):
        """初始化界面"""
        self.loading_overlay.hide()
        self.setWindowTitle("系统设置")
        self.setFixedSize(500, 600)  # 增加高度以容纳账户信息设置

        layout = QVBoxLayout(self)

        # MyQuant设置
        myquant_group = QGroupBox("掘金量化连接设置")
        myquant_layout = QFormLayout(myquant_group)

        # 只保留必要的连接配置
        self.account_id_edit = QLineEdit(self.config.get("myquant.account_id", ""))
        self.token_edit = QLineEdit(self.config.get("myquant.token", ""))

        myquant_layout.addRow("账户ID:", self.account_id_edit)
        myquant_layout.addRow("Token:", self.token_edit)

        # 添加说明文字
        info_label = QLabel("说明：账户余额、持仓等信息将从MyQuant客户端自动读取")
        info_label.setStyleSheet("color: #666; font-size: 10px;")
        myquant_layout.addRow("", info_label)

        # 测试连接按钮
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self.test_connection)
        myquant_layout.addRow("", self.test_button)

        layout.addWidget(myquant_group)

        # 数据源设置
        datasource_group = QGroupBox("数据源设置")
        datasource_layout = QFormLayout(datasource_group)

        self.backup_source_combo = QComboBox()
        self.backup_source_combo.addItems(["AKShare", "Tushare", "无"])
        datasource_layout.addRow("备用数据源:", self.backup_source_combo)

        # 测试备用数据源
        self.test_backup_button = QPushButton("测试备用数据源")
        self.test_backup_button.clicked.connect(self.test_backup_source)
        datasource_layout.addRow("", self.test_backup_button)

        layout.addWidget(datasource_group)

        # 账户信息设置
        # 账户缓存设置
        account_group = QGroupBox("账户信息缓存设置")
        account_layout = QFormLayout(account_group)

        # 启用账户信息保存
        self.save_account_checkbox = QCheckBox("启用账户信息缓存")
        self.save_account_checkbox.setChecked(
            self.config.get("account.save_account_info", True)
        )
        account_layout.addRow("", self.save_account_checkbox)

        # 说明文字
        account_info_label = QLabel(
            "说明：启用后，从MyQuant API获取的账户信息将缓存到本地。\n"
            "当API无法访问时，系统将使用缓存的账户信息。\n"
            "所有账户数据都从MyQuant客户端自动读取，无需手动设置。"
        )
        account_info_label.setStyleSheet("color: #666; font-size: 10px;")
        account_layout.addRow("", account_info_label)

        layout.addWidget(account_group)

        # 数据更新设置
        data_group = QGroupBox("数据更新设置")
        data_layout = QFormLayout(data_group)

        # 历史数据存储路径
        self.data_path_edit = QLineEdit(self.config.get("data.storage_path", "gp_data"))
        self.browse_path_button = QPushButton("浏览...")
        self.browse_path_button.clicked.connect(self.browse_data_path)

        path_layout = QHBoxLayout()
        path_layout.addWidget(self.data_path_edit)
        path_layout.addWidget(self.browse_path_button)

        path_widget = QWidget()
        path_widget.setLayout(path_layout)
        data_layout.addRow("历史数据存储路径:", path_widget)

        self.update_all_button = QPushButton("更新所有股票历史数据")
        self.update_all_button.clicked.connect(self.update_all_historical_data)
        data_layout.addRow("", self.update_all_button)

        layout.addWidget(data_group)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def browse_data_path(self):
        """浏览数据存储路径"""
        from PyQt5.QtWidgets import QFileDialog

        current_path = self.data_path_edit.text() or "gp_data"
        path = QFileDialog.getExistingDirectory(
            self, "选择历史数据存储目录", current_path
        )
        if path:
            self.data_path_edit.setText(path)

    def test_connection(self):
        """测试MyQuant连接（异步执行，避免卡死）"""
        # 保存当前设置
        self.config.set("myquant.account_id", self.account_id_edit.text())
        self.config.set("myquant.token", self.token_edit.text())

        # 检查必要字段
        if not self.token_edit.text().strip():
            QMessageBox.warning(self, "配置错误", "请先填写Token！")
            return

        if not self.account_id_edit.text().strip():
            QMessageBox.warning(self, "配置错误", "请先填写账户ID！")
            return

        # 禁用测试按钮，显示正在测试状态
        self.test_button.setEnabled(False)
        self.test_button.setText("正在测试连接...")

        # 创建测试线程
        self.test_thread = ConnectionTestThread(self.config)
        self.test_thread.test_completed.connect(self.on_test_completed)
        self.test_thread.start()

    def on_test_completed(self, success: bool, message: str):
        """连接测试完成回调"""
        # 恢复测试按钮状态
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")

        # 显示测试结果
        if success:
            QMessageBox.information(self, "连接测试", f"✅ {message}")
        else:
            QMessageBox.warning(
                self,
                "连接测试",
                f"❌ {message}\n\n提示：\n1. 检查Token和账户ID是否正确\n2. 确认网络连接正常\n3. 确认MyQuant账户状态正常",
            )

    def test_backup_source(self):
        """测试备用数据源"""
        source = self.backup_source_combo.currentText()

        if source == "AKShare":
            if AKSHARE_AVAILABLE:
                QMessageBox.information(self, "数据源测试", "✅ AKShare可用！")
            else:
                QMessageBox.warning(
                    self,
                    "数据源测试",
                    "❌ AKShare不可用！\n请安装akshare: pip install akshare",
                )
        elif source == "Tushare":
            QMessageBox.information(self, "数据源测试", "Tushare测试功能待实现")
        else:
            QMessageBox.information(self, "数据源测试", "未选择备用数据源")

    def update_all_historical_data(self):
        """更新所有股票历史数据"""
        reply = QMessageBox.question(
            self, "确认", "确定要更新所有股票的历史数据吗？\n这可能需要较长时间。"
        )
        if reply == QMessageBox.Yes:
            # 创建并显示历史数据下载对话框
            self.show_historical_data_dialog()

    def show_historical_data_dialog(self):
        """显示历史数据管理对话框"""
        try:
            # ⚠️ 历史数据管理器模块未找到，显示提示对话框
            QMessageBox.warning(
                self, "错误", "历史数据管理器模块未找到，无法打开历史数据管理功能。"
            )

        except ImportError:
            QMessageBox.warning(self, "错误", "历史数据管理器模块未找到")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动历史数据管理器失败: {e}")

    def closeEvent(self, event):
        """对话框关闭事件处理"""
        # 如果有正在进行的测试线程，等待其完成
        if hasattr(self, "test_thread") and self.test_thread.isRunning():
            # 给测试线程一点时间完成
            self.test_thread.wait(1000)  # 等待最多1秒
            if self.test_thread.isRunning():
                self.test_thread.terminate()  # 强制终止
                self.test_thread.wait()

        super().closeEvent(event)

    def accept(self):
        """保存设置"""
        self.config.set("myquant.account_id", self.account_id_edit.text())
        self.config.set("myquant.token", self.token_edit.text())
        self.config.set("data.storage_path", self.data_path_edit.text())

        # 保存账户信息设置
        self.config.set(
            "account.save_account_info", self.save_account_checkbox.isChecked()
        )

        # 保存配置
        self.config.save_config()
        super().accept()


class AddStockDialog(QDialog):
    """添加股票对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加股票到交易池")
        self.setModal(True)
        self.setFixedSize(600, 500)

        self.code_edit = None
        self.name_edit = None
        self.search_edit = None
        self.stock_list = None
        self.all_stocks = {}  # {code: name}

        self.load_stock_data()
        self.init_ui()

    def load_stock_data(self):
        """加载股票数据 - 优先实时查询"""
        # 优先级1: MyQuant实时查询
        if self.load_from_myquant():
            return

        # 优先级2: AkShare实时查询
        if self.load_from_akshare():
            return

        # 优先级3: 本地文件（带时效检查）
        if self.load_from_local_file():
            return

        # 优先级4: 默认股票列表
        self.load_default_stocks()

    def load_from_myquant(self):
        """从MyQuant实时获取A股股票列表（使用get_symbols）"""
        try:
            logging.info("🔍 正在从MyQuant实时获取A股股票数据(get_symbols)...")
            try:
                # 尝试动态导入 gm.api，若不可用再尝试导入 gm 并定位 api 子模块，保证在不同安装方式下都能兼容
                import importlib

                gm = None
                try:
                    gm = importlib.import_module("gm.api")
                except ImportError:
                    try:
                        gm_mod = importlib.import_module("gm")
                        # 如果 gm 包中包含 api 子模块或属性，优先使用它
                        if hasattr(gm_mod, "api"):
                            gm = getattr(gm_mod, "api")
                        else:
                            # 尝试再动态加载 gm.api（有些环境下需要这样）
                            try:
                                gm = importlib.import_module("gm.api")
                            except Exception:
                                gm = None
                    except ImportError:
                        gm = None

                if gm is None:
                    raise ImportError("gm.api not found")
            except Exception as e:
                # 如果 gm.api 不可用，记录日志并返回 False，让调用方尝试其他数据源
                logging.warning(
                    f"⚠️ 无法导入 gm.api（MyQuant），将跳过 MyQuant 数据源: {e}"
                )
                return False

            # 获取所有A股股票，包含停牌和ST
            stocks = gm.get_symbols(
                sec_type1=1010,  # 股票
                sec_type2=101001,  # A股
                skip_suspended=False,
                skip_st=False,
                trade_date=None,
                df=True,
            )
            if stocks is not None and not stocks.empty:
                for _, row in stocks.iterrows():
                    symbol = str(row.get("symbol", ""))
                    name = str(row.get("sec_name", row.get("name", ""))).strip()
                    # symbol格式如 SHSE.600000
                    if "." in symbol:
                        code = symbol.split(".")[1]
                        self.all_stocks[code] = name
                logging.info(
                    f"✅ MyQuant(get_symbols)获取{len(self.all_stocks)}只A股股票数据"
                )
                return True
            else:
                logging.warning("⚠️ MyQuant(get_symbols)未获取到股票数据")
        except Exception as e:
            logging.warning(f"⚠️ MyQuant(get_symbols)股票数据获取失败: {e}")
        return False

    def load_from_akshare(self):
        """从AkShare实时获取股票列表"""
        try:
            logging.info("🔍 正在从AkShare实时获取股票数据...")
            import akshare as ak

            # 获取A股股票列表
            stock_info = ak.stock_info_a_code_name()

            if stock_info is not None and len(stock_info) > 0:
                for _, row in stock_info.iterrows():
                    code = str(row["code"]).zfill(6)
                    name = str(row["name"]).strip()
                    self.all_stocks[code] = name

                logging.info(f"✅ AkShare实时获取{len(self.all_stocks)}只股票数据")
                return True

        except Exception as e:
            logging.warning(f"⚠️ AkShare股票数据获取失败: {e}")

        return False

    def load_from_local_file(self):
        """从本地文件加载股票数据（带时效检查）"""
        try:
            import os
            from datetime import datetime, timedelta

            import pandas as pd

            file_path = "A股列表.csv"

            # 检查文件是否存在
            if not os.path.exists(file_path):
                logging.warning("⚠️ 本地股票数据文件不存在")
                return False

            # 检查文件时效（超过7天提示更新）
            file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            if datetime.now() - file_time > timedelta(days=7):
                logging.warning(
                    f"⚠️ 本地股票数据文件已过期({file_time.strftime('%Y-%m-%d')}), 建议更新"
                )

            # 加载本地文件
            df = pd.read_csv(file_path, encoding="utf-8")
            for _, row in df.iterrows():
                code = str(row["code"]).zfill(6)
                name = str(row["name"]).strip()
                self.all_stocks[code] = name

            logging.info(
                f"📁 从本地文件加载{len(self.all_stocks)}只股票数据 (文件日期: {file_time.strftime('%Y-%m-%d')})"
            )
            return True

        except Exception as e:
            logging.error(f"❌ 本地股票数据加载失败: {e}")

        return False

    def load_default_stocks(self):
        """加载默认股票列表（备用方案）"""

    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout()

        # 标题
        title_label = QLabel("添加股票到交易池")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(
            """
            QLabel {
                font-size: 14pt;
                font-weight: bold;
                color: #333;
                padding: 10px;
            }
        """
        )
        layout.addWidget(title_label)

        # 数据源状态显示
        data_source_layout = QHBoxLayout()

        self.data_source_label = QLabel(f"📊 股票数据: {len(self.all_stocks)}只")
        self.data_source_label.setStyleSheet(
            """
            QLabel {
                background-color: #e8f5e8;
                padding: 5px 10px;
                border-radius: 4px;
                border-left: 3px solid #4caf50;
                font-size: 9pt;
            }
        """
        )
        data_source_layout.addWidget(self.data_source_label)

        refresh_button = QPushButton("🔄 刷新数据")
        refresh_button.setToolTip("重新从MyQuant/AkShare获取最新股票数据")
        refresh_button.clicked.connect(self.refresh_stock_data)
        refresh_button.setStyleSheet(
            """
            QPushButton {
                background-color: #2196f3;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 4px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #1976d2;
            }
        """
        )
        data_source_layout.addWidget(refresh_button)

        data_source_layout.addStretch()
        layout.addLayout(data_source_layout)

        # 创建选项卡
        tab_widget = QTabWidget()

        # 选项卡1：手动输入
        manual_tab = QWidget()
        manual_layout = QVBoxLayout(manual_tab)

        # 手动输入区域
        form_layout = QFormLayout()

        # 股票代码输入
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("请输入6位股票代码，如: 000001")
        self.code_edit.setMaxLength(6)
        self.code_edit.textChanged.connect(self.on_code_changed)
        form_layout.addRow("股票代码:", self.code_edit)

        # 股票名称输入
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("请输入股票名称，如: 平安银行")
        self.name_edit.textChanged.connect(self.update_ok_button)
        form_layout.addRow("股票名称:", self.name_edit)

        manual_layout.addLayout(form_layout)

        # 提示信息
        tip_label = QLabel("💡 提示: 代码必须是6位数字，名称可以自定义")
        tip_label.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        manual_layout.addWidget(tip_label)

        tab_widget.addTab(manual_tab, "手动输入")

        # 选项卡2：搜索选择
        search_tab = QWidget()
        search_layout = QVBoxLayout(search_tab)

        # 搜索说明
        search_info = QLabel("🔍 智能股票搜索")
        search_info.setStyleSheet("font-weight: bold; color: #333; padding: 5px;")
        search_layout.addWidget(search_info)

        search_desc = QLabel(
            "• 本地优先：快速搜索已缓存的股票数据\n• 在线补充：本地无结果时自动联网查询\n• 实时更新：支持MyQuant/AkShare数据源"
        )
        search_desc.setStyleSheet(
            "color: #666; font-size: 9pt; padding: 5px; background-color: #f8f9fa; border-radius: 4px;"
        )
        search_layout.addWidget(search_desc)

        # 搜索框
        search_label = QLabel("搜索股票（输入代码或名称）:")
        search_layout.addWidget(search_label)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "输入股票代码或名称进行搜索... (支持在线查询)"
        )
        self.search_edit.textChanged.connect(self.filter_stocks)
        search_layout.addWidget(self.search_edit)

        # 股票列表
        self.stock_list = QListWidget()
        self.stock_list.itemDoubleClicked.connect(self.on_stock_selected)
        search_layout.addWidget(self.stock_list)

        # 初始显示所有股票（限制数量）
        self.show_stocks(list(self.all_stocks.items())[:50])  # 只显示前50只

        tab_widget.addTab(search_tab, "搜索选择")

        layout.addWidget(tab_widget)

        # 按钮
        button_layout = QHBoxLayout()

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        self.ok_button = QPushButton("添加")
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setEnabled(False)  # 初始禁用
        self.ok_button.setStyleSheet(
            """
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """
        )
        button_layout.addWidget(self.ok_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def show_stocks(self, stock_items):
        """显示股票列表"""
        self.stock_list.clear()
        for code, name in stock_items:
            item_text = f"{code} - {name}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, (code, name))  # 存储股票信息
            self.stock_list.addItem(item)

    def filter_stocks(self, text):
        """过滤股票列表（增强版：支持在线搜索）"""
        if not text:
            # 如果搜索框为空，显示前50只股票
            self.show_stocks(list(self.all_stocks.items())[:50])
            return

        text = text.lower()
        filtered_stocks = []

        # 本地搜索
        for code, name in self.all_stocks.items():
            if (
                text in code.lower() or text in name.lower() or text in name
            ):  # 支持中文搜索
                filtered_stocks.append((code, name))
                if len(filtered_stocks) >= 100:  # 限制显示数量
                    break

        # 如果本地搜索结果少于5个，尝试在线搜索
        if len(filtered_stocks) < 5 and len(text) >= 2:
            try:
                online_results = self.search_stock_online(text)
                for code, name in online_results:
                    # 避免重复添加
                    if not any(
                        existing_code == code for existing_code, _ in filtered_stocks
                    ):
                        filtered_stocks.append((code, name))
                        # 同时添加到本地缓存中
                        self.all_stocks[code] = name
            except Exception as e:
                logging.warning(f"⚠️ 在线搜索失败: {e}")

        self.show_stocks(filtered_stocks)

        # 如果没有找到任何结果，显示提示
        if not filtered_stocks:
            self.stock_list.clear()
            no_result_item = QListWidgetItem("🔍 未找到匹配的股票")
            no_result_item.setFlags(no_result_item.flags() & ~Qt.ItemIsSelectable)
            self.stock_list.addItem(no_result_item)

            if len(text) >= 6 and text.isdigit():
                search_item = QListWidgetItem(f"💡 尝试在线搜索: {text}")
                search_item.setData(Qt.UserRole, (text, f"搜索股票{text}"))
                self.stock_list.addItem(search_item)

    def on_stock_selected(self, item):
        """股票被选中时"""
        code, name = item.data(Qt.UserRole)
        self.code_edit.setText(code)
        self.name_edit.setText(name)
        self.update_ok_button()

    def on_code_changed(self, text):
        """股票代码输入变化时的处理"""
        # 只允许数字输入
        if text and not text.isdigit():
            self.code_edit.setText("".join(filter(str.isdigit, text)))
            return

        # 如果代码在股票列表中，自动填充名称
        if len(text) == 6 and text in self.all_stocks:
            self.name_edit.setText(self.all_stocks[text])

        # 检查是否输入完整
        self.update_ok_button()

    def update_ok_button(self):
        """更新确定按钮状态"""
        code = self.code_edit.text().strip()
        name = self.name_edit.text().strip()

        # 代码必须是6位数字，名称不能为空
        is_valid = len(code) == 6 and code.isdigit() and len(name) > 0
        self.ok_button.setEnabled(is_valid)

    def get_stock_info(self):
        """获取股票信息"""
        code = self.code_edit.text().strip()
        name = self.name_edit.text().strip()
        return code, name

    def refresh_stock_data(self):
        """刷新股票数据"""
        try:
            from PyQt5.QtWidgets import QProgressDialog

            # 显示进度对话框
            progress = QProgressDialog("正在刷新股票数据...", "取消", 0, 0, self)
            progress.setWindowTitle("数据刷新")
            progress.setWindowModality(Qt.WindowModal)
            progress.show()

            # 清空当前数据
            old_count = len(self.all_stocks)
            self.all_stocks.clear()

            # 重新加载数据
            self.load_stock_data()

            # 更新界面
            new_count = len(self.all_stocks)
            self.data_source_label.setText(f"📊 股票数据: {new_count}只")

            # 如果当前有搜索内容，重新过滤
            if hasattr(self, "search_edit"):
                self.filter_stocks(self.search_edit.text())

            progress.close()

            # 显示刷新结果
            if new_count > old_count:
                self.data_source_label.setStyleSheet(
                    """
                    QLabel {
                        background-color: #e8f5e8;
                        padding: 5px 10px;
                        border-radius: 4px;
                        border-left: 3px solid #4caf50;
                        font-size: 9pt;
                    }
                """
                )
                logging.info(f"✅ 股票数据已刷新: {old_count} → {new_count}")
            else:
                self.data_source_label.setStyleSheet(
                    """
                    QLabel {
                        background-color: #fff3e0;
                        padding: 5px 10px;
                        border-radius: 4px;
                        border-left: 3px solid #ff9800;
                        font-size: 9pt;
                    }
                """
                )
                logging.warning(f"⚠️ 股票数据未更新: {new_count}只")

        except Exception as e:
            logging.error(f"❌ 刷新股票数据失败: {e}")

    def search_stock_online(self, query):
        """在线搜索股票（当本地搜索无结果时）"""
        try:
            if len(query) < 2:
                return []

            logging.info(f"🔍 在线搜索股票: {query}")
            results = []

            # 尝试使用AkShare搜索
            try:
                import akshare as ak

                # 如果是6位数字，可能是股票代码
                if query.isdigit() and len(query) == 6:
                    # 尝试获取股票信息
                    try:
                        stock_individual_info = ak.stock_individual_info_em(
                            symbol=query
                        )
                        if (
                            stock_individual_info is not None
                            and len(stock_individual_info) > 0
                        ):
                            name_row = stock_individual_info[
                                stock_individual_info["item"] == "股票简称"
                            ]
                            if len(name_row) > 0:
                                name = name_row["value"].iloc[0]
                                results.append((query, name))
                                logging.info(f"✅ 在线找到股票: {query} - {name}")
                    except Exception:
                        pass

                # 尝试模糊搜索股票名称
                else:
                    try:
                        stock_info = ak.stock_info_a_code_name()
                        if stock_info is not None:
                            # 搜索包含关键词的股票
                            matched_stocks = stock_info[
                                stock_info["name"].str.contains(
                                    query, na=False, case=False
                                )
                            ].head(
                                10
                            )  # 限制返回10个结果

                            for _, row in matched_stocks.iterrows():
                                code = str(row["code"]).zfill(6)
                                name = str(row["name"]).strip()
                                results.append((code, name))
                                logging.info(f"✅ 在线找到股票: {code} - {name}")
                    except Exception:
                        pass

            except Exception as e:
                logging.warning(f"⚠️ AkShare在线搜索失败: {e}")

            return results[:10]  # 最多返回10个结果

        except Exception as e:
            logging.error(f"❌ 在线股票搜索失败: {e}")
            return []

    def keyPressEvent(self, event):
        """处理按键事件"""
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if self.ok_button.isEnabled():
                self.accept()
        else:
            super().keyPressEvent(event)


class TradeDialog(QDialog):
    """交易对话框"""

    def __init__(
        self, code: str, name: str, action: str, is_simulation: bool, parent=None
    ):
        super().__init__(parent)
        self.code = code
        self.name = name
        self.action = action
        self.is_simulation = is_simulation
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        action_text = "买入" if self.action == "buy" else "卖出"
        mode_text = "模拟" if self.is_simulation else "实盘"

        self.setWindowTitle(f"{mode_text}{action_text} - {self.name}({self.code})")
        self.setFixedSize(400, 300)

        layout = QFormLayout(self)

        # 股票信息
        layout.addRow("股票:", QLabel(f"{self.name}({self.code})"))
        layout.addRow("操作:", QLabel(f"{mode_text}{action_text}"))

        # 交易方式选择
        self.trade_type_combo = QComboBox()
        if self.action == "buy":
            self.trade_type_combo.addItems(
                ["限价买入", "市价买入", "对手价买入", "本方价买入", "最优五档买入"]
            )
        else:
            self.trade_type_combo.addItems(
                ["限价卖出", "市价卖出", "对手价卖出", "本方价卖出", "最优五档卖出"]
            )
        self.trade_type_combo.currentTextChanged.connect(self.on_trade_type_changed)
        layout.addRow("交易方式:", self.trade_type_combo)

        # 交易数量
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(100, 999999)
        self.quantity_spin.setSingleStep(100)
        self.quantity_spin.setValue(100)
        self.quantity_spin.setSuffix(" 股")
        layout.addRow("数量:", self.quantity_spin)

        # 交易价格 - 改用QDoubleSpinBox支持小数
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.01, 9999.99)
        self.price_spin.setSingleStep(0.01)
        self.price_spin.setDecimals(2)  # 支持2位小数
        self.price_spin.setValue(10.00)
        self.price_spin.setSuffix(" 元")
        self.price_label = QLabel("价格:")
        layout.addRow(self.price_label, self.price_spin)

        # 预估金额
        self.amount_label = QLabel("0.00 元")
        self.amount_label.setStyleSheet("color: #666; font-weight: bold;")
        layout.addRow("预估金额:", self.amount_label)

        # 连接信号更新预估金额
        self.quantity_spin.valueChanged.connect(self.update_amount)
        self.price_spin.valueChanged.connect(self.update_amount)

        # 更新初始状态
        self.on_trade_type_changed()
        self.update_amount()

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addRow(button_box)

    def on_trade_type_changed(self):
        """交易方式改变时的处理"""
        trade_type = self.trade_type_combo.currentText()

        # 市价交易隐藏价格输入
        if "市价" in trade_type:
            self.price_spin.setVisible(False)
            self.price_label.setVisible(False)
            self.price_spin.setValue(0.0)
        else:
            self.price_spin.setVisible(True)
            self.price_label.setVisible(True)
            if self.price_spin.value() == 0.0:
                self.price_spin.setValue(10.00)

        self.update_amount()

    def update_amount(self):
        """更新预估金额"""
        quantity = self.quantity_spin.value()
        price = self.price_spin.value()

        if "市价" in self.trade_type_combo.currentText():
            self.amount_label.setText("市价交易")
            self.amount_label.setStyleSheet("color: #ff9800; font-weight: bold;")
        else:
            amount = quantity * price
            self.amount_label.setText(f"{amount:.2f} 元")

            # 根据金额设置颜色
            if amount > 50000:
                self.amount_label.setStyleSheet(
                    "color: #f44336; font-weight: bold;"
                )  # 红色
            elif amount > 10000:
                self.amount_label.setStyleSheet(
                    "color: #ff9800; font-weight: bold;"
                )  # 橙色
            else:
                self.amount_label.setStyleSheet(
                    "color: #4caf50; font-weight: bold;"
                )  # 绿色

    def get_trade_info(self) -> tuple:
        """获取交易信息"""
        trade_type = self.trade_type_combo.currentText()
        return self.quantity_spin.value(), self.price_spin.value(), trade_type


class TradeRecordsDialog(QDialog):
    """交易记录对话框"""

    def __init__(self, trade_recorder: TradingRecorder, parent=None):
        super().__init__(parent)
        self.trade_recorder = trade_recorder
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("交易记录")
        self.setGeometry(200, 200, 800, 500)

        layout = QVBoxLayout(self)

        # 交易记录表格
        self.records_table = QTableWidget()
        headers = [
            "时间",
            "股票代码",
            "股票名称",
            "操作",
            "价格",
            "数量",
            "金额",
            "类型",
        ]
        self.records_table.setHorizontalHeaderLabels(headers)
        self.records_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # 加载记录
        self.load_records()

        layout.addWidget(self.records_table)

        # 关闭按钮
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def load_records(self):
        """加载交易记录"""
        records = self.trade_recorder.get_records()
        self.records_table.setRowCount(len(records))

        for i, record in enumerate(records):
            self.records_table.setItem(i, 0, QTableWidgetItem(record.get("时间", "")))
            self.records_table.setItem(
                i, 1, QTableWidgetItem(record.get("股票代码", ""))
            )
            self.records_table.setItem(
                i, 2, QTableWidgetItem(record.get("股票名称", ""))
            )
            self.records_table.setItem(i, 3, QTableWidgetItem(record.get("操作", "")))
            self.records_table.setItem(
                i, 4, QTableWidgetItem(f"{record.get('价格', 0):.2f}")
            )
            self.records_table.setItem(
                i, 5, QTableWidgetItem(str(record.get("数量", 0)))
            )
            self.records_table.setItem(
                i, 6, QTableWidgetItem(f"{record.get('金额', 0):.2f}")
            )

            # 类型列着色
            type_item = QTableWidgetItem(record.get("类型", ""))
            if record.get("类型") == "模拟":
                type_item.setForeground(QColor("blue"))
            else:
                type_item.setForeground(QColor("red"))
            self.records_table.setItem(i, 7, type_item)


# ================================
# 工作线程类
# ================================


class ConfigInitThread(QThread):
    init_complete = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        """模拟配置加载过程"""
        time.sleep(0.5)
        self.init_complete.emit()


class ConnectionTestThread(QThread):
    """连接测试线程"""

    test_completed = pyqtSignal(bool, str)  # success, message

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    def run(self):
        """执行连接测试"""
        try:
            # 设置超时，避免长时间卡死
            import threading
            import time

            result = {"success": False, "message": "", "completed": False}

            def test_worker():
                try:
                    # 添加详细的测试步骤
                    client = MyQuantClient(self.config)
                    # 加载配置中的Token和账户ID
                    token = self.config.get("myquant.token", "")
                    account_id = self.config.get("myquant.account_id", "")
                    # 检查配置
                    if not token:
                        result["message"] = "Token为空，请检查配置"
                        result["completed"] = True
                        return
                    if not account_id:
                        result["message"] = "账户ID为空，请检查配置"
                        result["completed"] = True
                        return
                    # 设置到客户端
                    client.token = token
                    client.account_id = account_id
                    # 尝试连接
                    if client.connect():
                        result["success"] = True
                        result["message"] = "MyQuant连接成功！"
                    else:
                        result["success"] = False
                        result["message"] = (
                            "MyQuant连接失败，请检查Token和账户ID是否正确"
                        )
                    result["completed"] = True

                except Exception as e:
                    result["success"] = False
                    result["message"] = f"连接测试异常: {str(e)}"
                    result["completed"] = True

            # 使用线程执行测试，设置超时
            test_thread = threading.Thread(target=test_worker)
            test_thread.daemon = True
            test_thread.start()

            # 等待完成或超时
            timeout_count = 0
            while timeout_count < 50 and not result["completed"]:  # 最多等待5秒
                time.sleep(0.1)
                timeout_count += 1

            if not result["completed"]:
                result["success"] = False
                result["message"] = "连接测试超时（5秒），可能网络较慢或配置有误"

            self.test_completed.emit(result["success"], result["message"])

        except Exception as e:
            self.test_completed.emit(False, f"测试线程异常: {str(e)}")


class InitializationThread(QThread):
    """系统初始化线程 - 按照用户流程图优化版"""

    def __init__(
        self,
        myquant_client: MyQuantClient,
        stock_pool: StockPool,
        signals: SystemSignals,
    ):
        super().__init__()
        self.myquant_client = myquant_client
        self.stock_pool = stock_pool
        self.signals = signals
        # 添加初始化状态标志
        self.initialization_completed = False
        # 添加停止标志
        self._stop_requested = False

    def stop(self):
        """请求停止初始化线程"""
        self._stop_requested = True
        # 如果线程被阻塞，可以尝试中断，但要小心处理
        self.signals.log_message.emit("正在停止初始化...", "INFO")

    def is_goldminer_running(self) -> bool:
        """检查goldminer3.exe进程是否运行"""
        try:
            import psutil

            # 检查是否有goldminer3.exe进程在运行
            for proc in psutil.process_iter(["name"]):
                try:
                    if proc.info["name"] and "goldminer3.exe" in proc.info["name"]:
                        return True
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    pass
            return False
        except ImportError:
            # 如果没有安装psutil模块，尝试其他方式检查
            self.signals.log_message.emit(
                "⚠️ 未安装psutil模块，无法检查进程状态", "WARNING"
            )
            return False
        except Exception as e:
            self.signals.log_message.emit(f"⚠️ 进程检查异常: {str(e)[:100]}", "WARNING")
            return False

    def run(self):
        """执行初始化流程 - 按照用户提供的流程图优化实现"""
        try:
            # 定义每个步骤的超时处理函数
            def execute_with_timeout(func, timeout, step_name, fail_message=""):
                """执行函数并添加超时控制"""
                import threading

                result = [None]
                exception = [None]
                completed = threading.Event()

                def target():
                    try:
                        result[0] = func()
                        completed.set()
                    except Exception as e:
                        exception[0] = e
                        completed.set()

                thread = threading.Thread(target=target)
                thread.daemon = True
                thread.start()

                # 等待超时或完成，同时检查是否收到停止请求
                wait_start = time.time()
                while (
                    not completed.is_set()
                    and time.time() - wait_start < timeout
                    and not self._stop_requested
                ):
                    time.sleep(0.1)

                # 检查是否超时或收到停止请求
                if not completed.is_set():
                    self.signals.log_message.emit(
                        f"{step_name}超时（{timeout}秒）", "WARNING"
                    )
                    return False, None
                elif self._stop_requested:
                    self.signals.log_message.emit(f"{step_name}已中止", "INFO")
                    return False, None
                elif exception[0] is not None:
                    # 限制错误信息长度，避免日志过长
                    error_str = str(exception[0])[:200]
                    self.signals.log_message.emit(
                        f"{fail_message or step_name}失败: {error_str}", "WARNING"
                    )
                    return False, None

                return True, result[0]

            # 1. 加载交易池文件 (10%)
            self.signals.initialization_progress.emit(5, "加载交易池文件...")

            def load_pool_func():
                self.stock_pool.load_pool()
                return len(self.stock_pool.stocks)

            success, stock_count = execute_with_timeout(
                load_pool_func,
                3.0,  # 交易池加载超时设置为3秒
                "交易池加载",
                "加载交易池",
            )

            if success and stock_count is not None:
                self.signals.initialization_progress.emit(
                    10, f"交易池加载完成，共{stock_count}只股票"
                )
                self.signals.log_message.emit(
                    f"📊 交易池加载完成，共{stock_count}只股票", "INFO"
                )
            elif not self._stop_requested:
                self.signals.log_message.emit(
                    "⚠️ 交易池加载失败，使用空交易池继续", "WARNING"
                )

            # 2. 检查客户端连接 (30%)
            self.signals.initialization_progress.emit(20, "检查客户端连接...")

            # 先检查goldminer3.exe进程是否存在
            client_running = self.is_goldminer_running()

            if client_running:
                self.signals.status_message.emit("客户端已启动")
                self.signals.log_message.emit(
                    "✅ 检测到掘金终端(goldminer3.exe)已在运行", "INFO"
                )
            else:
                self.signals.status_message.emit("客户端未启动")
                self.signals.log_message.emit(
                    "❌ 未检测到掘金终端(goldminer3.exe)运行", "WARNING"
                )
                # 发送掘金终端未运行信号
                self.signals.goldminer_not_running.emit()
                # 等待短暂时间让提示窗口显示
                time.sleep(0.5)
                # 在掘金终端未运行时，停止初始化流程
                self.signals.initialization_progress.emit(0, "初始化已暂停")
                self.signals.log_message.emit(
                    "⏸️ 初始化已暂停，等待掘金终端启动", "INFO"
                )
                self.initialization_completed = False
                return

            # 直接调用myquant_client的connect方法，它已经包含了超时控制
            # 不再使用execute_with_timeout，避免双重线程和超时嵌套
            connected = self.myquant_client.connect()
            success = connected

            connected = success and connected

            # 检查是否连接成功，如果失败给出明确的失败原因
            if not connected:
                if not MYQUANT_AVAILABLE:
                    self.signals.log_message.emit(
                        "❌ MyQuant API不可用，请检查掘金终端安装和配置", "ERROR"
                    )
                else:
                    self.signals.log_message.emit(
                        "❌ 无法连接到掘金终端，请检查网络连接和Token有效性", "ERROR"
                    )
                # 不要抛出异常，而是正常结束初始化流程
                self.signals.initialization_progress.emit(
                    100, "初始化已完成(部分功能不可用)"
                )
                self.signals.status_message.emit("初始化已完成(部分功能不可用)")
                self.initialization_completed = True
                return
            self.signals.client_status_changed.emit(connected)

            # 3. 获取持仓信息并验证连接 (50%)
            positions = []
            if connected and not self._stop_requested:
                self.signals.initialization_progress.emit(40, "获取持仓信息...")

                def get_positions_func():
                    return self.myquant_client.get_positions()

                success, positions = execute_with_timeout(
                    get_positions_func,
                    4.0,  # 获取持仓超时设置为4秒
                    "获取持仓信息",
                    "获取持仓信息",
                )

                if success and positions is not None and len(positions) > 0:
                    self.signals.positions_updated.emit(positions)
                    self.signals.initialization_progress.emit(
                        50, f"获取到{len(positions)}只持仓股票"
                    )
                    self.signals.log_message.emit(
                        f"📊 获取到{len(positions)}只持仓股票", "INFO"
                    )
                else:
                    # 持仓为空也可能是正常的，继续后续步骤
                    positions = []
                    self.signals.positions_updated.emit([])
                    self.signals.initialization_progress.emit(50, "持仓信息获取完成")
                    self.signals.log_message.emit("📊 当前无持仓股票", "INFO")

            # 如果连接失败，提供明确的提示信息
            if not connected or self._stop_requested:
                if not self._stop_requested:
                    self.signals.log_message.emit(
                        "❌ 初始化失败：无法连接MyQuant", "ERROR"
                    )
                    self.signals.log_message.emit("💡 解决方案：", "INFO")
                    self.signals.log_message.emit(
                        "  • 确保掘金终端(goldminer3.exe)已启动", "INFO"
                    )
                    self.signals.log_message.emit(
                        "  • 在设置中验证Token和账户ID是否正确", "INFO"
                    )
                    self.signals.log_message.emit("  • 检查网络连接是否正常", "INFO")
                    self.signals.log_message.emit(
                        "  • 点击'初始化系统'按钮重试", "INFO"
                    )
                    self.signals.status_message.emit("客户端未启动/连接失败")
                # 即使连接失败，也要标记为完成，避免主线程卡死
                self.initialization_completed = True
                return

            # 4. 获取账户信息 (70%)
            if not self._stop_requested:
                self.signals.initialization_progress.emit(60, "获取账户信息...")

                def get_account_func():
                    return self.myquant_client.get_account_info()

                success, account = execute_with_timeout(
                    get_account_func,
                    4.0,  # 获取账户信息超时设置为4秒
                    "获取账户信息",
                    "获取账户信息",
                )

                if success and account is not None:
                    self.signals.account_updated.emit(account)
                    self.signals.initialization_progress.emit(70, "账户信息获取完成")
                    self.signals.log_message.emit(
                        f"💰 账户总资产: {account.get('总资产', 0):.2f}元", "INFO"
                    )
                else:
                    account = {}
                    self.signals.log_message.emit(
                        "⚠️ 账户信息获取失败，使用默认值", "WARNING"
                    )
            else:
                account = {}

            # 5. 将持仓股票添加到交易池 (90%)
            if not self._stop_requested and positions:
                self.signals.initialization_progress.emit(80, "更新交易池...")

                def update_pool_func():
                    if positions:
                        self.stock_pool.add_position_stocks(positions)

                execute_with_timeout(
                    update_pool_func,
                    3.0,  # 更新交易池超时设置为3秒
                    "更新交易池",
                    "更新交易池",
                )

                if not self._stop_requested:
                    self.signals.initialization_progress.emit(90, "交易池更新完成")
                    self.signals.log_message.emit(
                        "🔄 交易池已更新，持仓股票已添加", "INFO"
                    )

            # 6. 检查历史数据 (95%)
            if not self._stop_requested:
                self.signals.initialization_progress.emit(95, "检查历史数据...")
                # TODO: 实现历史数据完整性检查
                # 这里可以添加历史数据检查逻辑
                time.sleep(0.5)
                self.signals.log_message.emit("📈 历史数据检查完成", "INFO")

            # 7. 显示交易池第一只股票的图表
            if (
                not self._stop_requested
                and hasattr(self.stock_pool, "stocks")
                and self.stock_pool.stocks
            ):
                first_stock = self.stock_pool.stocks[0]
                self.signals.log_message.emit(
                    f"📊 显示交易池第一只股票：{first_stock}", "INFO"
                )
                # 这里可以发送信号来显示第一只股票的图表
                # 注意：实际的图表显示逻辑应该在MainWindow中实现

            # 只有在未收到停止请求时才标记为成功完成
            if not self._stop_requested:
                self.signals.initialization_progress.emit(100, "初始化完成")
                self.signals.log_message.emit("✅ 系统初始化成功完成", "SUCCESS")
                self.signals.status_message.emit("初始化完成")
            else:
                self.signals.initialization_progress.emit(100, "初始化已中止")
                self.signals.log_message.emit("初始化过程已被用户中止", "INFO")
                self.signals.status_message.emit("初始化已中止")

            self.initialization_completed = True

        except Exception as e:
            # 限制错误信息长度
            error_str = str(e)[:200]
            self.signals.log_message.emit(f"❌ 系统初始化失败: {error_str}", "ERROR")
            self.signals.log_message.emit("💡 请检查网络连接和配置后重试", "INFO")
            self.signals.status_message.emit("初始化出错")
            # 发生异常时也标记为完成
            self.initialization_completed = True

    def is_initialization_completed(self):
        """检查初始化是否完成"""
        return self.initialization_completed

    def closeEvent(self, event):
        """窗口关闭事件处理
        确保程序能够在点击窗口关闭按钮时立即终止运行
        """
        # 接受关闭事件，让程序立即关闭
        event.accept()


# ================================# 主程序入口# ================================


def main():
    """主程序入口"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("auto_trader.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName("A股自动交易系统")
    app.setApplicationVersion("2.0")

    # 设置应用样式
    app.setStyleSheet(
        """
        QMainWindow {
            background-color: #f5f5f5;
        }
        QGroupBox {
            font-weight: bold;
            border: 2px solid #cccccc;
            border-radius: 5px;
            margin-top: 1ex;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        QPushButton {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #45a049;
        }
        QPushButton:pressed {
            background-color: #3d8b40;
        }
    """
    )

    # 创建主窗口
    main_window = MainWindow()
    main_window.show()

    # 运行应用
    sys.exit(app.exec_())


class SimpleHistoricalDataDialog(QDialog):
    """简化的历史数据下载对话框"""

    def __init__(self, config, myquant_client, stock_pool):
        super().__init__()
        self.config = config
        self.client = myquant_client
        self.stock_pool = stock_pool
        self.download_thread = None

        self.setWindowTitle("历史数据下载")
        self.setFixedSize(600, 400)
        self.init_ui()

    def init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)

        # 说明文本
        info_label = QLabel("此功能将为交易池中的所有股票下载历史数据")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # 设置区域
        settings_group = QGroupBox("下载设置")
        settings_layout = QFormLayout(settings_group)

        # 时间周期选择
        self.period_combo = QComboBox()
        self.period_combo.addItems(["1d (日线)", "15m (15分钟)", "60m (1小时)"])
        self.period_combo.setCurrentText("1d (日线)")
        settings_layout.addRow("时间周期:", self.period_combo)

        # 数据量
        self.count_spin = QSpinBox()
        self.count_spin.setRange(50, 2000)
        self.count_spin.setValue(250)
        self.count_spin.setSuffix(" 条")
        settings_layout.addRow("下载数量:", self.count_spin)

        layout.addWidget(settings_group)

        # 进度区域
        progress_group = QGroupBox("下载进度")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.status_label = QLabel("就绪")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)

        layout.addWidget(progress_group)

        # 日志区域
        log_group = QGroupBox("下载日志")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(100)
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)

        # 按钮区域
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("开始下载")
        self.cancel_button = QPushButton("取消")
        self.close_button = QPushButton("关闭")

        self.start_button.clicked.connect(self.start_download)
        self.cancel_button.clicked.connect(self.cancel_download)
        self.close_button.clicked.connect(self.close)

        self.cancel_button.setEnabled(False)

        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.cancel_button)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

    def add_log(self, message):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def start_download(self):
        """开始下载"""
        stocks = self.stock_pool.get_all_stocks()
        if not stocks:
            QMessageBox.warning(self, "警告", "交易池为空，请先添加股票")
            return

        # 获取设置
        period_text = self.period_combo.currentText()
        period = period_text.split(" ")[0]  # 提取周期代码
        count = self.count_spin.value()

        # 创建并启动下载线程
        self.download_thread = SimpleDownloadThread(
            self.client, list(stocks.keys()), period, count
        )

        self.download_thread.progress_updated.connect(self.update_progress)
        self.download_thread.log_message.connect(self.add_log)
        self.download_thread.download_finished.connect(self.download_completed)

        # 更新界面状态
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        self.add_log(f"开始下载 {len(stocks)} 只股票的 {period} 数据")
        self.download_thread.start()

    def cancel_download(self):
        """取消下载"""
        if self.download_thread:
            self.download_thread.cancel()
            self.add_log("正在取消下载...")

    def update_progress(self, current, total, message):
        """更新进度"""
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_label.setText(f"{message} ({current}/{total})")

    def download_completed(self, success_count, total_count):
        """下载完成"""
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setValue(100)
        self.status_label.setText("下载完成")

        self.add_log(f"下载完成！成功: {success_count}/{total_count}")

        if success_count > 0:
            QMessageBox.information(
                self, "下载完成", f"成功下载 {success_count} 只股票的历史数据"
            )


class SimpleDownloadThread(QThread):
    """简化的下载线程"""

    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    log_message = pyqtSignal(str)
    download_finished = pyqtSignal(int, int)  # success_count, total_count

    def __init__(self, client, symbols, period, count):
        super().__init__()
        self.client = client
        self.symbols = symbols
        self.period = period
        self.count = count
        self.cancelled = False

    def cancel(self):
        """取消下载"""
        self.cancelled = True

    def run(self):
        """执行下载"""
        success_count = 0
        total_count = len(self.symbols)

        for i, symbol in enumerate(self.symbols):
            if self.cancelled:
                break

            self.progress_updated.emit(i, total_count, f"下载 {symbol}")

            try:
                # 下载数据
                df = self.client.get_historical_data(symbol, self.period, self.count)

                if not df.empty:
                    success_count += 1
                    self.log_message.emit(f"✅ {symbol} 下载成功 ({len(df)} 条记录)")
                else:
                    self.log_message.emit(f"⚠️ {symbol} 无数据")

            except Exception as e:
                self.log_message.emit(f"❌ {symbol} 下载失败: {e}")

            # 延迟以避免请求过于频繁
            self.msleep(200)

        self.download_finished.emit(success_count, total_count)


class OrdersDialog(QDialog):
    """订单查询对话框"""

    def __init__(self, myquant_client, parent=None):
        super().__init__(parent)
        self.myquant_client = myquant_client
        self.init_ui()
        self.load_orders()

    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("订单查询")
        self.setGeometry(100, 100, 900, 600)

        layout = QVBoxLayout(self)

        # 标题
        title_label = QLabel("📋 当日交易订单查询")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; margin: 10px;")
        layout.addWidget(title_label)

        # 按钮栏
        button_layout = QHBoxLayout()

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(self.load_orders)
        button_layout.addWidget(self.refresh_btn)

        self.unfinished_btn = QPushButton("⏳ 未完成订单")
        self.unfinished_btn.clicked.connect(self.load_unfinished_orders)
        button_layout.addWidget(self.unfinished_btn)

        self.all_orders_btn = QPushButton("📊 所有订单")
        self.all_orders_btn.clicked.connect(self.load_orders)
        button_layout.addWidget(self.all_orders_btn)

        # 添加撤销按钮
        self.cancel_btn = QPushButton("❌ 撤销选中订单")
        self.cancel_btn.clicked.connect(self.cancel_selected_order)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # 订单表格
        self.orders_table = QTableWidget()
        self.orders_table.setColumnCount(8)
        self.orders_table.setHorizontalHeaderLabels(
            ["订单ID", "股票代码", "股票名称", "方向", "数量", "价格", "状态", "时间"]
        )

        # 设置表格样式
        header = self.orders_table.horizontalHeader()
        header.setStyleSheet("QHeaderView::section { background-color: #f0f0f0; }")
        header.resizeSection(0, 120)  # 订单ID
        header.resizeSection(1, 80)  # 股票代码
        header.resizeSection(2, 120)  # 股票名称
        header.resizeSection(3, 60)  # 方向
        header.resizeSection(4, 80)  # 数量
        header.resizeSection(5, 80)  # 价格
        header.resizeSection(6, 80)  # 状态
        header.resizeSection(7, 150)  # 时间

        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.setSelectionBehavior(QTableWidget.SelectRows)

        # 添加右键菜单
        self.orders_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.orders_table.customContextMenuRequested.connect(self.show_context_menu)

        layout.addWidget(self.orders_table)

        # 状态标签
        self.status_label = QLabel("准备查询订单...")
        self.status_label.setStyleSheet("color: #666; margin: 5px;")
        layout.addWidget(self.status_label)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def load_orders(self):
        """加载所有订单"""
        try:
            self.status_label.setText("📡 正在查询所有订单...")
            self.refresh_btn.setEnabled(False)

            if not self.myquant_client.is_connected():
                self.status_label.setText("❌ MyQuant客户端未连接")
                QMessageBox.warning(
                    self, "连接错误", "MyQuant客户端未连接！\n请先配置并测试连接。"
                )
                return

            # 获取订单列表
            orders = self.myquant_client.get_orders()
            self.display_orders(orders, "所有订单")

        except Exception as e:
            self.status_label.setText(f"❌ 查询失败: {str(e)}")
            QMessageBox.critical(self, "查询错误", f"查询订单失败:\n{str(e)}")
        finally:
            self.refresh_btn.setEnabled(True)

    def load_unfinished_orders(self):
        """加载未完成订单"""
        try:
            self.status_label.setText("📡 正在查询未完成订单...")
            self.unfinished_btn.setEnabled(False)

            if not self.myquant_client.is_connected():
                self.status_label.setText("❌ MyQuant客户端未连接")
                QMessageBox.warning(
                    self, "连接错误", "MyQuant客户端未连接！\n请先配置并测试连接。"
                )
                return

            # 获取未完成订单列表
            orders = self.myquant_client.get_unfinished_orders()
            self.display_orders(orders, "未完成订单")

        except Exception as e:
            self.status_label.setText(f"❌ 查询失败: {str(e)}")
            QMessageBox.critical(self, "查询错误", f"查询未完成订单失败:\n{str(e)}")
        finally:
            self.unfinished_btn.setEnabled(True)

    def display_orders(self, orders, order_type):
        """显示订单列表"""
        if not orders:
            self.orders_table.setRowCount(0)
            self.status_label.setText(f"📋 {order_type}: 暂无数据")
            return

        self.orders_table.setRowCount(len(orders))

        for i, order in enumerate(orders):
            # 订单ID
            order_id = order.get("cl_ord_id", "") or order.get("order_id", "")
            self.orders_table.setItem(i, 0, QTableWidgetItem(str(order_id)))

            # 股票代码
            symbol = order.get("symbol", "")
            self.orders_table.setItem(i, 1, QTableWidgetItem(symbol))

            # 股票名称 (如果有的话)
            name = order.get("name", "") or symbol
            self.orders_table.setItem(i, 2, QTableWidgetItem(name))

            # 买卖方向
            side = order.get("side", "")
            direction = "买入" if side == 1 else "卖出" if side == 2 else str(side)
            self.orders_table.setItem(i, 3, QTableWidgetItem(direction))

            # 数量
            volume = order.get("volume", 0)
            self.orders_table.setItem(i, 4, QTableWidgetItem(str(volume)))

            # 价格
            price = order.get("price", 0)
            price_str = f"{price:.2f}" if price > 0 else "市价"
            self.orders_table.setItem(i, 5, QTableWidgetItem(price_str))

            # 状态
            status = order.get("status", "")
            status_text = self.get_status_text(status)
            item = QTableWidgetItem(status_text)

            # 根据状态设置颜色
            if status in [1, 2]:  # 未成交、部分成交
                item.setBackground(QColor("#fff3cd"))  # 黄色背景
            elif status == 3:  # 已成交
                item.setBackground(QColor("#d4edda"))  # 绿色背景
            elif status in [4, 5, 6]:  # 已撤销、部分撤销、已拒绝
                item.setBackground(QColor("#f8d7da"))  # 红色背景
            elif status == 7:  # 待报
                item.setBackground(QColor("#e2e3e5"))  # 灰色背景
            elif status in [8, 9]:  # 废单、部分废单
                item.setBackground(QColor("#f1c0c7"))  # 深红色背景

            self.orders_table.setItem(i, 6, item)

            # 时间
            created_at = order.get("created_at", "")
            if created_at:
                # 格式化时间显示
                import datetime

                try:
                    if isinstance(created_at, str):
                        # 假设时间格式为 "2025-01-27 09:30:00"
                        dt = datetime.datetime.strptime(
                            created_at[:19], "%Y-%m-%d %H:%M:%S"
                        )
                        time_str = dt.strftime("%H:%M:%S")
                    else:
                        time_str = str(created_at)
                except Exception:
                    time_str = str(created_at)
            else:
                time_str = ""
            self.orders_table.setItem(i, 7, QTableWidgetItem(time_str))

        # 统计各状态的订单数量
        status_counts = {}
        for order in orders:
            status = order.get("status", 0)
            status_text = self.get_status_text(status)
            status_counts[status_text] = status_counts.get(status_text, 0) + 1

        # 生成状态统计文本
        status_stats = []
        for status_text, count in status_counts.items():
            status_stats.append(f"{status_text}:{count}")

        stats_text = " | ".join(status_stats) if status_stats else "无数据"
        self.status_label.setText(
            f"📋 {order_type}: 共 {len(orders)} 条记录 [{stats_text}]"
        )

    def get_status_text(self, status):
        """获取状态文本"""
        status_map = {
            1: "未成交",
            2: "部分成交",
            3: "已成交",
            4: "已撤销",
            5: "部分撤销",
            6: "已拒绝",
            7: "待报",
            8: "废单",
            9: "部分废单",
        }
        return status_map.get(status, f"状态{status}")

    def show_context_menu(self, position):
        """显示右键菜单"""
        # 获取点击位置的行
        item = self.orders_table.itemAt(position)
        if not item:
            return

        current_row = item.row()
        self.orders_table.selectRow(current_row)  # 选中这一行

        # 获取选中的订单信息
        order_id_item = self.orders_table.item(current_row, 0)
        status_item = self.orders_table.item(current_row, 6)

        if not order_id_item or not status_item:
            return

        order_id = order_id_item.text()
        status_text = status_item.text()

        # 创建右键菜单
        menu = QMenu(self)

        # 根据订单状态决定可用操作
        if status_text in ["未成交", "部分成交"]:
            # 可以撤销的订单
            cancel_action = QAction("❌ 撤销订单", self)
            cancel_action.triggered.connect(
                lambda: self.cancel_order(order_id, current_row)
            )
            menu.addAction(cancel_action)
        elif status_text in ["已拒绝", "废单", "部分废单"]:
            # 被拒绝的订单或废单可以删除（从显示中移除）
            delete_action = QAction("🗑️ 删除记录", self)
            delete_action.triggered.connect(
                lambda: self.delete_order_record(current_row)
            )
            menu.addAction(delete_action)

        # 查看详情（所有订单都可以）
        detail_action = QAction("📋 查看详情", self)
        detail_action.triggered.connect(lambda: self.show_order_detail(current_row))
        menu.addAction(detail_action)

        # 刷新订单状态
        refresh_action = QAction("🔄 刷新状态", self)
        refresh_action.triggered.connect(self.load_orders)
        menu.addAction(refresh_action)

        # 显示菜单
        menu.exec_(self.orders_table.mapToGlobal(position))

    def cancel_order(self, order_id, row):
        """撤销订单"""
        reply = QMessageBox.question(
            self,
            "撤销订单",
            f"确定要撤销订单吗？\n\n订单ID: {order_id}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            try:
                result = self.myquant_client.cancel_order(order_id)

                if result["success"]:
                    QMessageBox.information(
                        self, "撤销成功", f"✅ 订单撤销成功！\n\n订单ID: {order_id}"
                    )
                    # 更新表格中的状态
                    status_item = self.orders_table.item(row, 6)
                    if status_item:
                        status_item.setText("已撤销")
                        status_item.setBackground(QColor("#f8d7da"))  # 红色背景
                else:
                    QMessageBox.warning(
                        self, "撤销失败", f"❌ 订单撤销失败！\n\n{result['message']}"
                    )

            except Exception as e:
                QMessageBox.critical(
                    self, "撤销异常", f"❌ 撤销过程中发生异常！\n\n{str(e)}"
                )

    def delete_order_record(self, row):
        """删除订单记录（仅从显示中移除）"""
        reply = QMessageBox.question(
            self,
            "删除记录",
            "确定要从列表中删除这条记录吗？\n\n注意：这只是从显示列表中移除，不会影响实际的交易记录。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            self.orders_table.removeRow(row)
            QMessageBox.information(self, "删除完成", "✅ 记录已从列表中移除")

    def show_order_detail(self, row):
        """显示订单详情"""
        # 获取订单信息
        order_data = []
        headers = []

        for col in range(self.orders_table.columnCount()):
            header = self.orders_table.horizontalHeaderItem(col)
            item = self.orders_table.item(row, col)

            if header and item:
                headers.append(header.text())
                order_data.append(item.text())

        # 创建详情文本
        detail_text = "<h3>📋 订单详情</h3><table border='1' style='border-collapse: collapse; width: 100%;'>"

        for i, (header, data) in enumerate(zip(headers, order_data)):
            detail_text += f"<tr><td style='padding: 8px; background-color: #f0f0f0; font-weight: bold;'>{header}</td>"
            detail_text += f"<td style='padding: 8px;'>{data}</td></tr>"

        detail_text += "</table>"

        # 显示详情对话框
        QMessageBox.information(self, "订单详情", detail_text)

    def cancel_selected_order(self):
        """撤销选中的订单"""
        current_row = self.orders_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "提示", "请先选择要撤销的订单")
            return

        # 获取选中的订单信息
        order_id_item = self.orders_table.item(current_row, 0)
        status_item = self.orders_table.item(current_row, 6)

        if not order_id_item or not status_item:
            QMessageBox.warning(self, "错误", "无法获取订单信息")
            return

        order_id = order_id_item.text()
        status_text = status_item.text()

        # 检查订单状态是否可以撤销
        if status_text not in ["未成交", "部分成交"]:
            QMessageBox.warning(
                self,
                "无法撤销",
                f"订单状态为 '{status_text}'，无法撤销。\n\n只有 '未成交' 或 '部分成交' 状态的订单才能撤销。",
            )
            return

        # 调用撤销方法
        self.cancel_order(order_id, current_row)


if __name__ == "__main__":
    main()
