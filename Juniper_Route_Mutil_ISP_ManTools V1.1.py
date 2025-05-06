import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import pandas as pd
import paramiko
import threading
import os
import re
import time
from datetime import datetime

'''
本程序主要功能：
1、针对juniper路由器开发。主要用做BGP多线接入路由器的路由、线路操作。
2、支持SSH、TELNET方式，自定义端口。设备IP、用户名、密码在excel表格中定义，支持多台设备选择。暂不支持实时密码输入（可自己修改，在选择设备后再手工输入密码）。
3、针对BGP多线出口路由器进行静态路由和黑洞路由发布、删除、查询。支持多线路预定义(excel)表格中读取，来进行指定ISP发由、接收路由的查询。暂时不支
   持从设备中读取线路名称。因为BGP group中可能对应多个neighbor地址，无法区ISP名称。 默认待查询的路由地址段为8.8.8.8/24，请自行修改。
4、基于预定义的policy-options，读取预定义的prefix-list，进行查询、增加、删除。在policy中调用prefix-list，实现特定地址段的路由策略进、出
  方向的控制。比如常见的允许、拒绝、增删改各种BGP属性（community、tag、metric等）。
5、基于预定义的fireware策略（策略名inside-outside-fbf，请自行修改） ，读取预定义term名称，暂时只支持源地址的查询、增加、删除。 fireware策略中，可以进出源、目地址、协议的操作。比如
   允许、拒绝、修改下一跳、修改路由实例、QOS属性等。
6、自定义命令输入功能，可以自行输入命令。或从commands.txt中预定义了多个命令，单独运行指定命令，或进行批量命令巡检。
7、基于python内置tkinter开发。。未对界面做过多美化。

'''
layout_padx = 5
layout_pady = 5


class JuniperRouteQueryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Juniper_Route_Mutil_ISP_ManTools V1.0")
        self.root.geometry("900x860")  # 增大窗口尺寸

        # 设备数据存储
        self.devices = None
        self.current_device_info = None
        self.current_ssh_session = None  # 当前SSH会话
        self.current_shell = None  # 当前shell通道

        self.line_prefix_list_dict = {}
        self.line_selected_prefix = None  # Currently selected prefix name
        self.line_selected_ips = []  # Currently selected IPs

        # 默认Excel文件名
        self.default_excel = "device_route.xlsx"
        self.default_command_txt = "commands.txt"
        # 创建界面元素
        self.create_main_interface()

        # 如果默认文件存在，自动加载
        if os.path.exists(self.default_excel):
            self.file_path.set(self.default_excel)
            self.load_devices()

        if not os.path.exists(self.default_command_txt):
            # 创建一个空文件
            with open(self.default_command_txt, "w") as file:
                pass

    def create_main_interface(self):
        # 创建主框架
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # 创建笔记本控件
        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # 创建输出显示标签页
        self.create_output_tab()

        # 输出部分
        output_frame = ttk.LabelFrame(self.root, text="****输出结果****", padding=10)
        output_frame.pack(fill=tk.BOTH, expand=True, padx=layout_padx, pady=layout_pady)

        self.output_text = scrolledtext.ScrolledText(
            output_frame,
            wrap=None,
            width=100,
            height=20,
            state='normal',
            font=('Consolas', 9),
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

        # 状态栏
        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN).pack(fill=tk.X, padx=layout_padx,
                                                                                  pady=layout_pady)

        # 查询状态
        self.query_in_progress = False

    def read_cmd_predefined_commands(self, file_path="commands.txt"):
        """从本地文件读取预定义命令列表"""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                # 读取所有行，去除每行首尾的空白字符
                lines = [line.strip() for line in file.readlines()]
                # 过滤掉空行和注释行（以#开头的行）
                commands = [line for line in lines if line and not line.startswith('#')]
                return commands
        except FileNotFoundError:
            self.append_output( f"警告: 未找到文件 {file_path}，命令下发模块将将使用空命令列表")
            return []
        except Exception as e:
            self.append_output(f"读取命令文件时出错: {e}，将使用空命令列表")
            return []

    def create_output_tab(self):
        """创建输出显示标签页"""
        self.output_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.output_tab, text="输出显示")

        # 创建选项卡式输出框
        self.output_notebook = ttk.Notebook(self.notebook)
        self.output_notebook.pack(fill=tk.BOTH, expand=True)

        # 为每个功能创建输出框
        self.function_outputs = {
            "路由信息查询": self.create_output_box("路由信息查询"),
            "常用命令下发": self.create_output_box("常用命令下发"),
            "公网线路调整": self.create_output_box("公网线路调整"),
            "强制线路调整": self.create_output_box("强制线路调整"),
            "路由发布管理": self.create_output_box("路由发布管理"),
            "黑洞路由管理": self.create_output_box("黑洞路由管理"),
            "关于": self.create_output_box("关于")
        }

    def create_output_box(self, tab_name):
        """为每个功能创建一个输出框"""
        tab = ttk.Frame(self.output_notebook)
        self.output_notebook.add(tab, text=tab_name)

        # 只在"路由信息查询"选项卡中定义文字输出
        if tab_name == "路由信息查询":
            # 文件选择部分
            file_frame = ttk.LabelFrame(tab, text="****设备信息文件****", padding=layout_padx)
            file_frame.pack(fill=tk.X, padx=layout_padx, pady=layout_pady)

            self.file_path = tk.StringVar()
            ttk.Entry(file_frame, textvariable=self.file_path, width=20).grid(row=0, column=0, padx=5)
            ttk.Button(file_frame, text="浏览设备文件", command=self.browse_file).grid(row=0, column=1, padx=5)
            ttk.Button(file_frame, text="加载设备信息", command=self.load_devices).grid(row=0, column=2, padx=5)
            ttk.Button(file_frame, text="退出", command=self.root.quit).grid(row=0, column=3,padx=5)
            # 设备选择部分
            select_frame = ttk.LabelFrame(tab, text="****选择设备和线路***", padding=5)
            select_frame.pack(fill=tk.X, padx=layout_padx, pady=layout_pady)

            ttk.Label(select_frame, text="设备名:").grid(row=0, column=0, sticky=tk.W)
            self.device_combo = ttk.Combobox(select_frame, state="readonly", width=20)
            self.device_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
            self.device_combo.bind("<<ComboboxSelected>>", self.on_device_select)

            ttk.Label(select_frame, text="线路名:").grid(row=0, column=2, sticky=tk.W)
            self.line_combo = ttk.Combobox(select_frame, state="readonly", width=20)
            self.line_combo.grid(row=0, column=3, sticky=tk.W, padx=5)
            self.line_combo.bind("<<ComboboxSelected>>", self.on_line_select)

            # 显示线路IP
            ttk.Label(select_frame, text="线路IP:").grid(row=0, column=4, sticky=tk.W)
            self.line_ip_label = ttk.Label(select_frame, text="", foreground="blue", width=20)
            self.line_ip_label.grid(row=0, column=5, sticky=tk.W, padx=5)

            # 分割线
            ttk.Separator(tab, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
            # 创建Notebook用于多标签页
            self.notebook = ttk.Notebook(tab)
            self.notebook.pack(fill=tk.BOTH, padx=layout_padx, pady=layout_pady)

            # 1. 路由表查询标签页
            self.create_route_table_tab()

            # 2. 发布路由查询标签页
            self.create_advertise_route_tab()

            # 3. 接收路由查询标签页
            self.create_receive_route_tab()

        if tab_name == "常用命令下发":

            # 自定义命令相关
            self.cmd_predefined_commands = self.read_cmd_predefined_commands()  # 从文件读取预定义命令
            self.cmd_custom_command_values = self.cmd_predefined_commands.copy()  # Combobox的值列表
            self.cmd_custom_command_values.append("")  # 添加空字符串以便手工输入
            # 自定义命令部分
            cmd_custom_command_frame = ttk.LabelFrame(tab,
                                                  text="*****自定义命令*****   （如果输出结果较长，建议用no-more参数，或发空格命令继续输出）",
                                                  padding=10)
            cmd_custom_command_frame.pack(fill=tk.X, padx=layout_padx, pady=layout_pady)

            ttk.Label(cmd_custom_command_frame, text="输入命令:").grid(row=0, column=0, sticky=tk.W)
            self.cmd_custom_command_entry = ttk.Entry(cmd_custom_command_frame, width=35)
            self.cmd_custom_command_entry.grid(row=0, column=1, sticky=tk.W, padx=5)

            # 绑定事件，当值改变时触发
            self.cmd_custom_command_entry.bind("<<ComboboxSelected>>", self.cmd_on_command_select)

            ttk.Button(cmd_custom_command_frame, text="运行命令", command=self.run_custom_command).grid(row=0, column=2,
                                                                                                    padx=5)
            ttk.Button(cmd_custom_command_frame, text="手工巡检", command=self.manual_inspection).grid(row=0, column=4,
                                                                                                   padx=5)
            ttk.Button(cmd_custom_command_frame, text="保存结果", command=self.save_result).grid(row=0, column=5, padx=5)


            # 创建选择和显示区域的框架
            self.cmd_selection_display_frame = tk.Frame(tab)
            self.cmd_selection_display_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            # 创建 cmd_prefix-list 选择区域（左侧）- 单选模式
            self.selection_frame = tk.LabelFrame(self.cmd_selection_display_frame, text="常用预定义命令 (单选)")
            self.selection_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            # 初始化 cmd_prefix-list 列表框，设置为单选模式
            self.cmd_cmd_name_listbox = tk.Listbox(self.selection_frame, height=10, selectmode=tk.SINGLE, exportselection=False)
            self.cmd_cmd_name_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            cmd_name_scrollbar = tk.Scrollbar(self.selection_frame)
            cmd_name_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.cmd_cmd_name_listbox.config(yscrollcommand=cmd_name_scrollbar.set)
            cmd_name_scrollbar.config(command=self.cmd_cmd_name_listbox.yview)

            # 清空并更新 cmd_prefix-list 列表框
            self.cmd_cmd_name_listbox.delete(0, tk.END)
            cmd_prefix_list_names = self.cmd_predefined_commands
            if cmd_prefix_list_names:
                for name in cmd_prefix_list_names:
                    self.cmd_cmd_name_listbox.insert(tk.END, name)

                # 默认选择第一个
                self.cmd_cmd_name_listbox.selection_set(0)
                self.cmd_selected_prefix = cmd_prefix_list_names[0]
            else:
                messagebox.showinfo("解析结果", "请确认commands.txt文件是否存在")
                # self.ip_listbox.delete(0, tk.END)
            # 绑定选择事件
            self.cmd_cmd_name_listbox.bind("<<ListboxSelect>>", self.cmd_on_prefix_select)

        if tab_name == "公网线路调整":
            # Top frame for command execution
            line_top_frame = ttk.Frame(tab)
            line_top_frame.pack(fill=tk.X, padx=5, pady=5)

            # Create selection and display areas
            self.line_selection_display_frame = tk.Frame(tab)
            self.line_selection_display_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            # Create prefix-list selection area (left) - single selection mode
            self.line_selection_frame = tk.LabelFrame(self.line_selection_display_frame,
                                                 text="Policy Prefix-list 选择 (单选)")
            self.line_selection_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.line_name_listbox = tk.Listbox(self.line_selection_frame, height=15,
                                           selectmode=tk.SINGLE, exportselection=False)
            self.line_name_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            line_name_scrollbar = tk.Scrollbar(self.line_selection_frame)
            line_name_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.line_name_listbox.config(yscrollcommand=line_name_scrollbar.set)
            line_name_scrollbar.config(command=self.line_name_listbox.yview)

            # Create IP address display area (middle) - multiple selection mode
            self.line_ip_frame = tk.LabelFrame(self.line_selection_display_frame,
                                          text="IP地址列表 (多选)")
            self.line_ip_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.line_ip_listbox = tk.Listbox(self.line_ip_frame, height=15,
                                         selectmode=tk.MULTIPLE, exportselection=False)
            self.line_ip_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            line_ip_scrollbar = tk.Scrollbar(self.line_ip_frame)
            line_ip_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.line_ip_listbox.config(yscrollcommand=line_ip_scrollbar.set)
            line_ip_scrollbar.config(command=self.line_ip_listbox.yview)

            # Create action buttons frame (right)
            self.line_action_frame = tk.LabelFrame(self.line_selection_display_frame,
                                              text="操作")
            self.line_action_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)

            # Add IP input area
            ttk.Label(self.line_action_frame, text="新增IP地址(每行一个):").pack(pady=5)
            self.line_ip_text = scrolledtext.ScrolledText(self.line_action_frame,
                                                     height=8, width=30)
            self.line_ip_text.pack(pady=5)

            # Add action buttons
            # Button to fetch prefix-list configuration
            ttk.Button(self.line_action_frame,
                       text="获取当前prefix-list配置",
                       command=self.line_fetch_prefix_list_config).pack(pady=5, fill=tk.X)
            ttk.Button(self.line_action_frame,
                       text="删除选中地址段",
                       command=self.line_delete_selected_ips).pack(pady=5, fill=tk.X)
            ttk.Button(self.line_action_frame,
                       text="增加地址段",
                       command=self.line_add_new_ips).pack(pady=5, fill=tk.X)

            # Bind selection events
            self.line_name_listbox.bind("<<ListboxSelect>>", self.line_on_prefix_select)
            self.line_ip_listbox.bind("<<ListboxSelect>>", self.line_on_ip_select)

            # Output area for commands
            self.line_cmd_output = scrolledtext.ScrolledText(tab, height=8)
            self.line_cmd_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.line_cmd_output.insert(tk.END, "策略命令输出将显示在这里...\n")

        if tab_name == "强制线路调整":
            # Top frame for command execution
            outside_top_frame = ttk.Frame(tab)
            outside_top_frame.pack(fill=tk.X, padx=5, pady=5)


            # Create selection and display areas
            self.outside_selection_display_frame = tk.Frame(tab)
            self.outside_selection_display_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            # Create prefix-list selection area (left) - single selection mode
            self.outside_selection_frame = tk.LabelFrame(self.outside_selection_display_frame,
                                                         text="Fireware Term 选择 (单选)")
            self.outside_selection_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.outside_name_listbox = tk.Listbox(self.outside_selection_frame, height=15,
                                                   selectmode=tk.SINGLE, exportselection=False)
            self.outside_name_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            outside_name_scrollbar = tk.Scrollbar(self.outside_selection_frame)
            outside_name_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.outside_name_listbox.config(yscrollcommand=outside_name_scrollbar.set)
            outside_name_scrollbar.config(command=self.outside_name_listbox.yview)

            # Create IP address display area (middle) - multiple selection mode
            self.outside_ip_frame = tk.LabelFrame(self.outside_selection_display_frame,
                                                  text="源IP地址列表 (多选)")
            self.outside_ip_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.outside_ip_listbox = tk.Listbox(self.outside_ip_frame, height=15,
                                                 selectmode=tk.MULTIPLE, exportselection=False)
            self.outside_ip_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            outside_ip_scrollbar = tk.Scrollbar(self.outside_ip_frame)
            outside_ip_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.outside_ip_listbox.config(yscrollcommand=outside_ip_scrollbar.set)
            outside_ip_scrollbar.config(command=self.outside_ip_listbox.yview)

            # Create action buttons frame (right)
            self.outside_action_frame = tk.LabelFrame(self.outside_selection_display_frame,
                                                      text="操作")
            self.outside_action_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)

            # Add IP input area
            ttk.Label(self.outside_action_frame, text="新增IP地址(每行一个):").pack(pady=5)
            self.outside_ip_text = scrolledtext.ScrolledText(self.outside_action_frame,
                                                             height=8, width=30)
            self.outside_ip_text.pack(pady=5)

            # Add action buttons
            # Button to fetch prefix-list configuration
            ttk.Button(self.outside_action_frame,
                       text="获取当前Firewall Term配置",
                       command=self.outside_fetch_prefix_list_config).pack(pady=5, fill=tk.X)
            ttk.Button(self.outside_action_frame,
                       text="删除选中源地址段",
                       command=self.outside_delete_selected_ips).pack(pady=5, fill=tk.X)
            ttk.Button(self.outside_action_frame,
                       text="增加源地址段",
                       command=self.outside_add_new_ips).pack(pady=5, fill=tk.X)

            # Bind selection events
            self.outside_name_listbox.bind("<<ListboxSelect>>", self.outside_on_prefix_select)
            self.outside_ip_listbox.bind("<<ListboxSelect>>", self.outside_on_ip_select)

            # Output area for commands
            self.outside_cmd_output = scrolledtext.ScrolledText(tab, height=8)
            self.outside_cmd_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.outside_cmd_output.insert(tk.END, "策略命令输出将显示在这里...\n")

        if tab_name == "路由发布管理":
            # Top frame for command execution
            route_top_frame = ttk.Frame(tab)
            route_top_frame.pack(fill=tk.X, padx=5, pady=5)

            # Create selection and display areas
            self.route_selection_display_frame = tk.Frame(tab)
            self.route_selection_display_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            # Create prefix-list selection area (left) - single selection mode
            self.route_selection_frame = tk.LabelFrame(self.route_selection_display_frame,
                                                       text="静态路由段 选择 (单选)")
            self.route_selection_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.route_name_listbox = tk.Listbox(self.route_selection_frame, height=15,
                                                 selectmode=tk.SINGLE, exportselection=False)
            self.route_name_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            route_name_scrollbar = tk.Scrollbar(self.route_selection_frame)
            route_name_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.route_name_listbox.config(yscrollcommand=route_name_scrollbar.set)
            route_name_scrollbar.config(command=self.route_name_listbox.yview)

            # Create IP address display area (middle) - multiple selection mode
            self.route_ip_frame = tk.LabelFrame(self.route_selection_display_frame,
                                                text="下一跳列表 (不用选择)")
            self.route_ip_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.route_ip_listbox = tk.Listbox(self.route_ip_frame, height=15,
                                               selectmode=tk.MULTIPLE, exportselection=False)
            self.route_ip_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            route_ip_scrollbar = tk.Scrollbar(self.route_ip_frame)
            route_ip_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.route_ip_listbox.config(yscrollcommand=route_ip_scrollbar.set)
            route_ip_scrollbar.config(command=self.route_ip_listbox.yview)

            # Create action buttons frame (right)
            self.route_action_frame = tk.LabelFrame(self.route_selection_display_frame,
                                                    text="操作")
            self.route_action_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)

            # Add IP input area
            ttk.Label(self.route_action_frame, text="新增地址段(每行一个):").pack(pady=5)
            self.route_ip_text = scrolledtext.ScrolledText(self.route_action_frame,
                                                           height=8, width=30)
            self.route_ip_text.pack(pady=5)

            # Add action buttons
            # Button to fetch prefix-list configuration
            ttk.Button(self.route_action_frame,
                       text="获取静态路由配置",
                       command=self.route_fetch_prefix_list_config).pack(pady=5, fill=tk.X)
            ttk.Button(self.route_action_frame,
                       text="删除选中路由地址",
                       command=self.route_delete_selected_ips).pack(pady=5, fill=tk.X)
            ttk.Button(self.route_action_frame,
                       text="增加新路由段",
                       command=self.route_add_new_ips).pack(pady=5, fill=tk.X)

            # Bind selection events
            self.route_name_listbox.bind("<<ListboxSelect>>", self.route_on_prefix_select)
            self.route_ip_listbox.bind("<<ListboxSelect>>", self.route_on_ip_select)

            # Output area for commands
            self.route_cmd_output = scrolledtext.ScrolledText(tab, height=8)
            self.route_cmd_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.route_cmd_output.insert(tk.END, "策略命令输出将显示在这里...\n")

        if tab_name == "黑洞路由管理":
            # Top frame for command execution
            bh_top_frame = ttk.Frame(tab)
            bh_top_frame.pack(fill=tk.X, padx=5, pady=5)

            # Create selection and display areas
            self.bh_selection_display_frame = tk.Frame(tab)
            self.bh_selection_display_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            # Create prefix-list selection area (left) - single selection mode
            self.bh_selection_frame = tk.LabelFrame(self.bh_selection_display_frame,
                                                    text="黑洞地址 选择 (单选)")
            self.bh_selection_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.bh_name_listbox = tk.Listbox(self.bh_selection_frame, height=15,
                                              selectmode=tk.SINGLE, exportselection=False)
            self.bh_name_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            bh_name_scrollbar = tk.Scrollbar(self.bh_selection_frame)
            bh_name_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.bh_name_listbox.config(yscrollcommand=bh_name_scrollbar.set)
            bh_name_scrollbar.config(command=self.bh_name_listbox.yview)

            # Create IP address display area (middle) - multiple selection mode
            self.bh_ip_frame = tk.LabelFrame(self.bh_selection_display_frame,
                                             text="下一跳列表 (不用选择)")
            self.bh_ip_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.bh_ip_listbox = tk.Listbox(self.bh_ip_frame, height=15,
                                            selectmode=tk.MULTIPLE, exportselection=False)
            self.bh_ip_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            bh_ip_scrollbar = tk.Scrollbar(self.bh_ip_frame)
            bh_ip_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.bh_ip_listbox.config(yscrollcommand=bh_ip_scrollbar.set)
            bh_ip_scrollbar.config(command=self.bh_ip_listbox.yview)

            # Create action buttons frame (right)
            self.bh_action_frame = tk.LabelFrame(self.bh_selection_display_frame,
                                                 text="操作")
            self.bh_action_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)

            # Add IP input area
            ttk.Label(self.bh_action_frame, text="新增黑洞地址(每行一个):").pack(pady=5)
            self.bh_ip_text = scrolledtext.ScrolledText(self.bh_action_frame,
                                                        height=8, width=30)
            self.bh_ip_text.pack(pady=5)

            # Add action buttons
            # Button to fetch prefix-list configuration
            ttk.Button(self.bh_action_frame,
                       text="获取当前黑洞配置",
                       command=self.bh_fetch_prefix_list_config).pack(pady=5, fill=tk.X)
            ttk.Button(self.bh_action_frame,
                       text="删除选中黑洞地址",
                       command=self.bh_delete_selected_ips).pack(pady=5, fill=tk.X)
            ttk.Button(self.bh_action_frame,
                       text="增加新黑洞地址",
                       command=self.bh_add_new_ips).pack(pady=5, fill=tk.X)

            # Bind selection events
            self.bh_name_listbox.bind("<<ListboxSelect>>", self.bh_on_prefix_select)
            self.bh_ip_listbox.bind("<<ListboxSelect>>", self.bh_on_ip_select)

            # Output area for commands
            self.bh_cmd_output = scrolledtext.ScrolledText(tab, height=8)
            self.bh_cmd_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.bh_cmd_output.insert(tk.END, "策略命令输出将显示在这里...\n")


        if tab_name == "关于":
            # 文本输出区域
            about_text = """     Juniper_Route_Mutil_ISP_ManTools v1.0

            版权所有 (C) 2025

            juniper路由器多线ISP路由器维护管理工具，
            提供了juniper设备预定义配置修改、配置备份、命令下发、巡检等功能。
            """

            output_frame = ttk.LabelFrame(tab, text=about_text, padding="10")
            output_frame.pack(fill=tk.BOTH, expand=True, pady=5)



    def line_fetch_prefix_list_config(self):
        """Fetch current prefix-list configuration from device"""
        if not self.validate_input():
            return

        # 使用更可靠的命令格式，确保获取完整配置
        command = "show configuration policy-options | display set | match prefix-list|no-more"
        self.current_callback = self.line_process_prefix_list_config  # 明确设置回调
        self.start_query(command)

    def line_process_prefix_list_config(self, output):
        """Process the prefix-list configuration output"""
        try:
            pattern = r"set policy-options prefix-list (\S+) (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})"
            self.line_prefix_list_dict = {}
            self.line_selected_prefix = None  # 重置当前选择
            self.line_selected_ips = []

            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue

                match = re.match(pattern, line)
                if match:
                    prefix_name = match.group(1)
                    ip_address = match.group(2)
                    if prefix_name in self.line_prefix_list_dict:
                        if ip_address not in self.line_prefix_list_dict[prefix_name]:
                            self.line_prefix_list_dict[prefix_name].append(ip_address)
                    else:
                        self.line_prefix_list_dict[prefix_name] = [ip_address]

            # 调试：打印解析结果
            # print("解析结果:")
            # for name, ips in self.line_prefix_list_dict.items():
            #     print(f"{name}: {', '.join(ips)}")

            # 更新UI显示
            self.line_update_prefix_list_ui()


        except Exception as e:
            messagebox.showerror("错误",
                                 f"处理配置时出错：{str(e)}\n" +
                                 "请检查raw_output.txt查看原始输出")


    def line_update_prefix_list_ui(self):
        """更新prefix-list的UI显示"""
        self.line_name_listbox.delete(0, tk.END)
        self.line_ip_listbox.delete(0, tk.END)

        if not self.line_prefix_list_dict:
            # messagebox.showinfo("提示", "未找到prefix-list配置")
            self.line_selected_prefix = None
            return

        # 排序显示
        sorted_names = sorted(self.line_prefix_list_dict.keys())
        for name in sorted_names:
            self.line_name_listbox.insert(tk.END, name)

        # 默认选择第一个
        if sorted_names:
            self.line_name_listbox.selection_set(0)
            self.line_selected_prefix = sorted_names[0]  # 同步到成员变量
            self.line_update_ip_list()

    def line_on_ip_select(self, event):
        """Handle IP address selection event - maintain left selection state"""
        # Get selected IPs
        self.line_selected_ips = [self.line_ip_listbox.get(i) for i in self.line_ip_listbox.curselection()]

        # Re-set left selection state
        if self.line_selected_prefix:
            items = self.line_name_listbox.get(0, tk.END)
            if self.line_selected_prefix in items:
                index = items.index(self.line_selected_prefix)
                self.line_name_listbox.selection_clear(0, tk.END)
                self.line_name_listbox.selection_set(index)
                self.line_name_listbox.see(index)

    def line_update_ip_list(self):
        """Update IP address list"""
        self.line_ip_listbox.delete(0, tk.END)
        self.line_selected_ips = []

        if self.line_selected_prefix and self.line_selected_prefix in self.line_prefix_list_dict:
            ips = sorted(self.line_prefix_list_dict[self.line_selected_prefix])
            for ip in ips:
                self.line_ip_listbox.insert(tk.END, ip)

    def line_delete_selected_ips(self):
        """Delete selected IP addresses from prefix-list"""
        if not self.line_selected_prefix or not self.line_selected_ips:
            messagebox.showwarning("警告", "请先选择要删除的IP地址")
            return

        # Check if we're trying to delete the last IP (1.1.1.1/32)
        remaining_ips = [ip for ip in self.line_prefix_list_dict[self.line_selected_prefix]
                         if ip not in self.line_selected_ips]

        if not remaining_ips:
            messagebox.showwarning("警告", "每个prefix-list必须至少保留一个IP地址")
            return

        # Generate delete commands
        commands = []
        for ip in self.line_selected_ips:
            if ip != "1.1.1.1/32":  # Skip the default IP
                cmd = f"delete policy-options prefix-list {self.line_selected_prefix} {ip}"
                commands.append(cmd)

        if not commands:
            messagebox.showinfo("提示", "没有有效的IP地址需要删除")
            return

        # Display commands and execute
        self.line_display_commands(commands)
        self.execute_config_commands(commands)

        # Refresh with proper timing
        self.root.after(3000, lambda: self.line_refresh_prefix_list())

    def line_add_new_ips(self):
        """Add new IP addresses to prefix-list"""
        if not self.line_selected_prefix:
            messagebox.showwarning("警告", "请先选择prefix-list")
            return

        new_ips = self.line_ip_text.get("1.0", tk.END).strip().splitlines()
        if not new_ips:
            messagebox.showwarning("警告", "请输入要添加的IP地址")
            return

        # Validate IP format
        ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$"
        line_valid_ips = [ip for ip in new_ips if re.match(ip_pattern, ip.strip())]

        if not line_valid_ips:
            messagebox.showwarning("警告", "没有有效的IP地址格式 (应为 x.x.x.x/x)")
            return

        # Generate set commands
        commands = [f"set policy-options prefix-list {self.line_selected_prefix} {ip}"
                    for ip in line_valid_ips]

        # Display commands and execute
        self.line_display_commands(commands)
        self.execute_config_commands(commands)

        # Clear input and refresh display with proper timing
        self.line_ip_text.delete("1.0", tk.END)
        self.root.after(3000, lambda: self.line_refresh_prefix_list())  # 增加延迟确保配置生效

    def line_refresh_prefix_list(self):
        """完整的配置刷新流程"""
        try:
            # 1. 保存当前状态
            state = self.line_save_current_state()

            # 2. 显示加载状态
            self.status_var.set("正在刷新配置...")

            # 3. 获取最新配置
            self.line_fetch_prefix_list_config()

            # 4. 等待配置加载完成
            self.root.after(1500, lambda: self.line_restore_state_after_refresh(state))

        except Exception as e:
            pass

    def line_save_current_state(self):
        """保存当前UI状态"""
        return {
            'line_selected_prefix': self.line_selected_prefix,
            'line_selected_ips': self.line_selected_ips.copy(),
            'scroll_position': self.line_name_listbox.yview(),
            'ip_scroll_position': self.line_ip_listbox.yview()
        }

    def line_restore_state_after_refresh(self, state):
        """从保存的状态恢复UI"""
        try:
            # 恢复选中状态
            if state['line_selected_prefix'] and state['line_selected_prefix'] in self.line_prefix_list_dict:
                items = self.line_name_listbox.get(0, tk.END)
                if state['line_selected_prefix'] in items:
                    index = items.index(state['line_selected_prefix'])
                    self.line_name_listbox.selection_clear(0, tk.END)
                    self.line_name_listbox.selection_set(index)
                    self.line_name_listbox.see(index)
                    self.line_on_prefix_select(None)


            # 恢复滚动位置
            self.line_name_listbox.yview_moveto(state['scroll_position'][0])
            self.line_ip_listbox.yview_moveto(state['ip_scroll_position'][0])

            self.status_var.set("配置已刷新")
        except Exception as e:
            self.bh_cmd_output.insert(tk.END, f"恢复状态时出错: {str(e)}")
            self.status_var.set("部分状态恢复失败")

    def line_restore_selection_after_refresh(self, line_last_selected):
        """刷新后恢复之前的选中状态"""
        try:
            if line_last_selected and line_last_selected in self.line_prefix_list_dict:
                items = self.line_name_listbox.get(0, tk.END)
                if line_last_selected in items:
                    index = items.index(line_last_selected)
                    self.line_name_listbox.selection_clear(0, tk.END)
                    self.line_name_listbox.selection_set(index)
                    self.line_name_listbox.see(index)
                    self.line_on_prefix_select(None)  # 触发IP列表更新
            self.status_var.set("配置已刷新")
        except Exception as e:
            self.bh_cmd_output.insert(tk.END, f"恢复选择状态时出错: {str(e)}")

    def execute_config_commands(self, commands):
        """更可靠的配置执行流程"""
        if not self.validate_input():
            return False

        try:
            self.status_var.set("正在应用配置...")

            # 使用exclusive模式防止其他会话干扰
            full_commands = ["configure exclusive"] + commands + ["commit", "exit"]

            # 执行命令并等待完成
            for cmd in full_commands:
                self.start_query(cmd)
                # 根据命令类型调整等待时间
                delay = 1.0 if "commit" in cmd else 0.5
                time.sleep(delay)

            # 验证配置是否生效
            if not self.verify_config_applied(commands):
                raise Exception("配置可能未完全应用")

            self.status_var.set("配置已提交")
            return True
        except Exception as e:
            self.status_var.set(f"配置失败: {str(e)}")
            messagebox.showerror("错误", f"配置应用失败: {str(e)}")
            return False

    def verify_config_applied(self, commands):
        """验证配置是否已应用"""
        # 这里可以添加具体的验证逻辑
        # 例如检查特定配置是否存在
        time.sleep(1)  # 给设备一点时间应用配置
        return True  # 简化实现，实际应根据需要实现

    def commit_config_changes(self):
        """Commit configuration changes"""
        if not self.validate_input():
            return

        commands = ["commit"]
        self.execute_config_commands(commands)

    def line_display_commands(self, commands):
        """Display commands in the output area"""
        self.line_cmd_output.config(state='normal')
        self.line_cmd_output.insert(tk.END, "\n将要执行的命令:\n")
        for cmd in commands:
            self.line_cmd_output.insert(tk.END, f"{cmd}\n")
        self.line_cmd_output.see(tk.END)
        self.line_cmd_output.config(state='disabled')

    def line_smart_refresh(self):
        """智能刷新方法，处理各种情况"""
        try:
            # 1. 检查当前状态
            if not self.validate_input():
                return False

            # 2. 保存当前状态
            state = self.line_save_current_state()

            # 3. 尝试获取最新配置
            attempts = 0
            max_attempts = 3
            while attempts < max_attempts:
                try:
                    self.line_fetch_prefix_list_config()
                    self.line_restore_state_after_refresh(state)
                    return True
                except Exception as e:
                    attempts += 1
                    self.bh_cmd_output.insert(tk.END, f"刷新尝试 {attempts}/{max_attempts} 失败: {str(e)}")
                    time.sleep(1)  # 等待设备恢复

            raise Exception(f"经过 {max_attempts} 次尝试后刷新失败")

        except Exception as e:
            self.status_var.set(f"刷新失败: {str(e)}")
            messagebox.showwarning("刷新警告", str(e))
            return False

    def line_smart_refresh_after_delete(self):
        """删除操作后的智能刷新"""
        try:
            # 保存当前选中的prefix-name
            line_last_selected = self.line_selected_prefix

            # 完全重新加载配置
            self.line_fetch_prefix_list_config()

            # 尝试恢复选中状态
            if line_last_selected:
                items = self.line_name_listbox.get(0, tk.END)
                if line_last_selected in items:
                    index = items.index(line_last_selected)
                    self.line_name_listbox.selection_set(index)
                    self.line_on_prefix_select()

            self.status_var.set("删除操作完成，配置已刷新")

        except Exception as e:
            messagebox.showwarning("刷新警告",
                                   f"自动刷新失败: {str(e)}\n"
                                   "请手动点击'获取当前配置'按钮")



    def cmd_on_command_select(self, event):
        """当自定义命令被选择时触发"""
        selected_command = self.cmd_custom_command_entry.get()
        self.status_var.set(f"选择的命令: {selected_command}")

    def run_custom_command(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        command = self.cmd_custom_command_entry.get()

        if not command and not self.cmd_selected_prefix:
            messagebox.showerror("错误", "请输入要执行的命令")
            return
        elif command and self.cmd_selected_prefix:
            command = self.cmd_custom_command_entry.get()
        else:
            command = self.cmd_selected_prefix

        self.start_query(command)

    def manual_inspection(self):
        """手工巡检功能"""
        if self.query_in_progress:
            messagebox.showwarning("警告", "当前有查询正在进行，请稍后再试")
            return

        if not self.validate_input():
            return

        if not self.cmd_predefined_commands:
            messagebox.showwarning("警告", "没有可用的预定义命令")
            return

        # 确认对话框
        if not messagebox.askyesno("确认",
                                   f"确定要对设备 {self.current_device_info['ip']} 执行手工巡检吗？\n将执行 {len(self.cmd_predefined_commands)} 条命令"):
            return

        # 创建保存文件对话框
        file_path = filedialog.asksaveasfilename(
            title="保存巡检结果",
            initialfile=f"juniper_inspection_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )

        if not file_path:
            return  # 用户取消了保存

        # 准备巡检命令
        inspection_commands = self.cmd_predefined_commands.copy()
        inspection_commands.insert(0, "set cli screen-length 0")  # 禁用分页显示
        inspection_commands.append("set cli screen-length 24")  # 恢复分页显示

        # 创建线程执行巡检
        threading.Thread(
            target=self.execute_inspection,
            args=(inspection_commands, file_path),
            daemon=True
        ).start()

    def execute_inspection(self, commands, file_path):
        """执行巡检命令并保存结果"""
        try:
            self.query_in_progress = True
            self.status_var.set("巡检中...")

            # 确保SSH会话已建立
            if not self.establish_ssh_session():
                raise Exception("无法建立SSH连接")

            shell = self.current_shell
            results = []

            self.append_output("\n🔍 开始设备巡检...\n")

            for cmd in commands:
                if not cmd:
                    continue

                self.append_output(f"\n✅ 执行命令: {cmd}\n")
                shell.send(cmd + '\n')
                time.sleep(1)  # 等待命令执行

                # 读取输出
                output = ""
                start_time = time.time()
                while time.time() - start_time < 10:  # 10秒超时
                    if shell.recv_ready():
                        data = shell.recv(65535).decode('utf-8', errors='ignore')
                        output += data
                        self.append_output(data)
                        results.append(data)
                    else:
                        time.sleep(0.1)

                # 如果是设置命令，不需要等待提示符
                if not cmd.startswith("set cli"):
                    # 等待提示符出现
                    while True:
                        if shell.recv_ready():
                            data = shell.recv(65535).decode('utf-8', errors='ignore')
                            output += data
                            self.append_output(data)
                            results.append(data)
                            if '>' in data or '#' in data:
                                break
                        else:
                            time.sleep(0.1)
                            if '>' in output or '#' in output:
                                break
                            if time.time() - start_time > 10:  # 10秒超时
                                break

                time.sleep(0.5)  # 命令间短暂延迟

            # 保存结果到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(results))

            self.append_output(f"\n✅ 巡检完成，结果已保存到: {file_path}\n")
            messagebox.showinfo("完成", f"巡检完成，结果已保存到:\n{file_path}")

        except Exception as e:
            self.append_output(f"\n⚠ 巡检过程中出错: {str(e)}\n")
            messagebox.showerror("错误", f"巡检过程中出错:\n{str(e)}")
        finally:
            self.query_in_progress = False
            self.status_var.set("就绪")

    def create_route_table_tab(self):
        """创建路由表查询标签页"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="路由表查询")

        # 查询部分
        query_frame = ttk.LabelFrame(tab, text="路由表查询", padding=10)
        query_frame.pack(fill=tk.X, padx=layout_padx, pady=layout_pady)

        ttk.Label(query_frame, text="查询IP段:").grid(row=0, column=0, sticky=tk.W)
        self.route_table_ip_entry = ttk.Entry(query_frame, width=30)
        self.route_table_ip_entry.grid(row=0, column=1, sticky=tk.W, padx=5)
        self.route_table_ip_entry.insert(0, "8.8.8.8/24")

        ttk.Button(query_frame, text="查询路由表", command=self.route_table_query).grid(row=0, column=2, padx=5)
        ttk.Button(query_frame, text="扩展查询", command=self.route_table_extensive_query).grid(row=0, column=3, padx=5)

    def create_advertise_route_tab(self):
        """创建发布路由查询标签页"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="发布路由查询")

        # 查询部分
        query_frame = ttk.LabelFrame(tab, text="发布路由查询", padding=10)
        query_frame.pack(fill=tk.X, padx=layout_padx, pady=layout_pady)

        ttk.Label(query_frame, text="查询IP段:").grid(row=0, column=0, sticky=tk.W)
        self.advertise_ip_entry = ttk.Entry(query_frame, width=30)
        self.advertise_ip_entry.grid(row=0, column=1, sticky=tk.W, padx=5)
        self.advertise_ip_entry.insert(0, "8.8.8.8/24")

        ttk.Button(query_frame, text="普通查询", command=self.advertise_normal_query).grid(row=0, column=2, padx=5)
        ttk.Button(query_frame, text="扩展查询", command=self.advertise_extensive_query).grid(row=0, column=3, padx=5)

    def create_receive_route_tab(self):
        """创建接收路由查询标签页"""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="接收路由查询")

        # 查询部分
        query_frame = ttk.LabelFrame(tab, text="接收路由查询", padding=10)
        query_frame.pack(fill=tk.X, padx=layout_padx, pady=layout_pady)

        ttk.Label(query_frame, text="查询IP段:").grid(row=0, column=0, sticky=tk.W)
        self.receive_ip_entry = ttk.Entry(query_frame, width=30)
        self.receive_ip_entry.grid(row=0, column=1, sticky=tk.W, padx=5)
        self.receive_ip_entry.insert(0, "8.8.8.8/24")

        ttk.Button(query_frame, text="普通查询", command=self.receive_normal_query).grid(row=0, column=2, padx=5)
        ttk.Button(query_frame, text="扩展查询", command=self.receive_extensive_query).grid(row=0, column=3, padx=5)

    def browse_file(self):
        filename = filedialog.askopenfilename(
            title="选择设备信息文件",
            filetypes=(("Excel文件", "*.xlsx"), ("所有文件", "*.*")),
            initialfile=self.default_excel
        )
        if filename:
            self.file_path.set(filename)

    def load_devices(self):
        if not self.file_path.get():
            messagebox.showerror("错误", "请先选择Excel文件")
            return

        try:
            self.devices = self.read_device_info(self.file_path.get())
            if self.devices:
                # 获取去重后的设备名称列表
                device_names = list(self.devices.keys())
                self.device_combo['values'] = device_names

                # 默认选择第一个设备（如果只有一台设备则自动选中）
                if len(device_names) == 1:
                    self.device_combo.current(0)
                    self.on_device_select()
                elif len(device_names) > 1:
                    # 如果有多个设备，不自动选择，等待用户选择
                    self.status_var.set(f"已加载 {len(device_names)} 台设备，请选择设备")
                    messagebox.showerror("错误", "检查到多台设备，请选择设备")
                else:
                    messagebox.showerror("错误", "⚠ Excel文件中没有有效的设备信息")
                    return

                device_count = len(device_names)
                line_count = sum(len(dev['lines']) for dev in self.devices.values())
                self.status_var.set(f"加载成功: {device_count}台设备, {line_count}条线路")
            else:
                self.status_var.set("⚠ 设备信息加载失败")
        except Exception as e:
            messagebox.showerror("错误", f"⚠ 加载设备信息出错: {str(e)}")
            self.status_var.set("⚠ 加载设备信息出错")

    def read_device_info(self, excel_file):
        try:
            df = pd.read_excel(excel_file)
            # 检查必要列是否存在
            required_columns = ['设备名称', '设备IP', '设备登陆方式', '设备登陆端口', '用户名', '密码', '线路名称',
                                '线路IP']
            for col in required_columns:
                if col not in df.columns:
                    messagebox.showerror("错误", f"Excel文件中缺少必要的列: {col}")
                    return None

            # 将数据转换为字典列表，按设备名称分组
            devices = {}
            for _, row in df.iterrows():
                device_name = row['设备名称']
                if device_name not in devices:
                    devices[device_name] = {
                        'ip': row['设备IP'],
                        'login_method': row['设备登陆方式'],
                        'port': int(row['设备登陆端口']),
                        'username': row['用户名'],
                        'password': row['密码'],
                        'lines': []  # 存储该设备的所有线路
                    }

                # 添加线路信息
                devices[device_name]['lines'].append({
                    'line_name': row['线路名称'],
                    'line_ip': row['线路IP']
                })

            return devices
        except Exception as e:
            messagebox.showerror("错误", f"读取Excel文件出错: {str(e)}")
            return None

    def on_device_select(self, event=None):
        device_name = self.device_combo.get()
        if device_name in self.devices:
            # 如果设备已更改，关闭当前SSH会话
            if self.current_device_info and self.current_device_info['ip'] != self.devices[device_name]['ip']:
                self.close_ssh_session()

            self.current_device_info = self.devices[device_name]
            lines = [line['line_name'] for line in self.current_device_info['lines']]
            self.line_combo['values'] = lines

            # 默认选择第一个线路
            if lines:
                self.line_combo.current(0)
                self.on_line_select()

            self.status_var.set(f"已选择设备: {device_name} (共{len(lines)}条线路)")

    def on_line_select(self, event=None):
        if not self.current_device_info or not self.line_combo.get():
            return

        selected_line = self.line_combo.get()
        for line in self.current_device_info['lines']:
            if line['line_name'] == selected_line:
                self.line_ip_label.config(text=line['line_ip'])
                break

    def validate_input(self):
        if not self.current_device_info:
            messagebox.showerror("错误", "检查到多台设备，请选择设备")
            return False

        if not self.line_combo.get():
            messagebox.showerror("错误", "请选择线路")
            return False

        return True

    def get_selected_line_ip(self):
        selected_line = self.line_combo.get()
        for line in self.current_device_info['lines']:
            if line['line_name'] == selected_line:
                return line['line_ip']
        return ''

    def close_ssh_session(self):
        """关闭当前SSH会话"""
        if self.current_ssh_session:
            try:
                if self.current_shell:
                    self.current_shell.close()
                    self.current_shell = None
                self.current_ssh_session.close()
                self.current_ssh_session = None
                self.append_output("\n🚨SSH会话已关闭\n")
            except Exception as e:
                self.append_output(f"⚠ 关闭SSH会话时出错: {str(e)}\n")

    def establish_ssh_session(self):
        """建立SSH会话"""
        if self.current_ssh_session and self.current_ssh_session.get_transport() and self.current_ssh_session.get_transport().is_active():
            return True  # 会话已存在且活跃

        try:
            self.append_output(f"正在连接设备 {self.current_device_info['ip']}:{self.current_device_info['port']}...\n")

            # 创建SSH客户端
            self.current_ssh_session = paramiko.SSHClient()
            self.current_ssh_session.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # 连接设备
            self.current_ssh_session.connect(
                hostname=self.current_device_info['ip'],
                port=self.current_device_info['port'],
                username=self.current_device_info['username'],
                password=self.current_device_info['password'],
                timeout=10
            )

            # 获取shell
            self.current_shell = self.current_ssh_session.invoke_shell()
            self.append_output(f"\n✅ 已成功连接到设备: {self.current_device_info['ip']}\n")
            # self.current_shell.recv(65535)
            return True
        except paramiko.AuthenticationException:
            self.append_output("\n🔒 认证失败：用户名或密码错误\n")
            return False
        except paramiko.SSHException as e:
            self.append_output(f"\n🚨 SSH连接异常：{str(e)}\n")
            return False
        except Exception as e:
            self.append_output(f"SSH连接失败: {str(e)}\n")
            self.close_ssh_session()
            return False

    # 路由表查询功能
    def route_table_query(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        ip_range = self.route_table_ip_entry.get()
        if not ip_range:
            messagebox.showerror("错误", "请输入要查询的IP段")
            return

        command = f'show route {ip_range}|no-more'
        self.start_query(command)

    def route_table_extensive_query(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        ip_range = self.route_table_ip_entry.get()
        if not ip_range:
            messagebox.showerror("错误", "请输入要查询的IP段")
            return

        command = f'show route {ip_range} extensive|no-more'
        self.start_query(command)

    # 发布路由查询功能
    def advertise_normal_query(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        line_ip = self.get_selected_line_ip()
        ip_range = self.advertise_ip_entry.get()
        if not ip_range:
            messagebox.showerror("错误", "请输入要查询的IP段")
            return

        command = f'show route {ip_range} advertising-protocol bgp {line_ip}|no-more'
        self.start_query(command)

    def advertise_extensive_query(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        line_ip = self.get_selected_line_ip()
        ip_range = self.advertise_ip_entry.get()
        if not ip_range:
            messagebox.showerror("错误", "请输入要查询的IP段")
            return

        command = f'show route {ip_range} advertising-protocol bgp {line_ip} extensive|no-more'
        self.start_query(command)

    # 接收路由查询功能
    def receive_normal_query(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        line_ip = self.get_selected_line_ip()
        ip_range = self.receive_ip_entry.get()
        if not ip_range:
            messagebox.showerror("错误", "请输入要查询的IP段")
            return

        command = f'show route {ip_range} receive-protocol bgp {line_ip}|no-more'
        self.start_query(command)

    def receive_extensive_query(self):
        if self.query_in_progress:
            return
        if not self.validate_input():
            return

        line_ip = self.get_selected_line_ip()
        ip_range = self.receive_ip_entry.get()
        if not ip_range:
            messagebox.showerror("错误", "请输入要查询的IP段")
            return

        command = f'show route {ip_range} receive-protocol bgp {line_ip} extensive|no-more'
        self.start_query(command)

    def start_query(self, command):
        self.output_text.config(state='normal')
        self.output_text.delete(1.0, tk.END)
        self.output_text.insert(tk.END, f"执行命令: {command}\n")
        self.output_text.see(tk.END)
        self.output_text.config(state='disabled')

        self.query_in_progress = True
        self.status_var.set("查询中...")

        # 在新线程中执行SSH命令
        threading.Thread(
            target=self.execute_ssh_command,
            args=(command,),
            daemon=True
        ).start()

    def execute_ssh_command(self, command):
        try:
            # 确保SSH会话已建立
            if not self.establish_ssh_session():
                raise Exception("⚠ 无法建立SSH连接")

            # 获取shell
            shell = self.current_shell

            # 发送命令
            # self.append_output(f"执行命令: {command}\n")
            shell.send(command + '\n')
            time.sleep(2)

            # 读取输出
            output = ""
            while True:
                if shell.recv_ready():
                    data = shell.recv(65535).decode('utf-8', errors='ignore')
                    output += data
                    output = output.replace('\r', '')
                    self.append_output(data)

                    # 检查命令是否结束
                    if '>' in data or '#' in data:
                        break
                else:
                    break

            self.append_output("\n✅查询完成。\n")
            # 执行回调
            if hasattr(self, 'current_callback') and callable(self.current_callback):
                self.root.after(0, lambda: self.current_callback(output))

        except Exception as e:
            self.append_output(f"\n⚠ 发生错误: {str(e)}\n")
            self.close_ssh_session()
        finally:
            self.query_complete()

    def append_output(self, text):
        # 使用after方法确保GUI更新在主线程中执行
        self.root.after(0, lambda: self._append_output_helper(text))

    def _append_output_helper(self, text):
        self.output_text.config(state='normal')
        self.output_text.insert(tk.END, text)
        self.output_text.see(tk.END)
        self.output_text.config(state='disabled')
        # 将文本追加到日志文件
        self.log_to_file(text)

    def query_complete(self):
        self.query_in_progress = False
        self.status_var.set("✅查询完成")

    def log_to_file(self, text):
        try:
            with open('query_log.txt', 'a', encoding='utf-8') as log_file:
                log_file.write(text)
        except Exception as e:
            self.append_output(f"⚠ 日志写入失败: {str(e)}\n")

    def save_result(self):
        """保存查询结果到文件"""
        if not self.output_text.get(1.0, tk.END).strip():
            messagebox.showwarning("⚠️ 警告", "⚠ 没有查询结果可保存")
            return

        # 生成默认文件名
        default_filename = f"juniper_query_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        file_path = filedialog.asksaveasfilename(
            title="保存查询结果",
            initialfile=default_filename,
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.output_text.get(1.0, tk.END))
                messagebox.showinfo("成功", f"✅ 结果已保存到:\n{file_path}")
            except Exception as e:
                messagebox.showerror("错误", f"⚠ 保存文件时出错:\n{str(e)}")

    def __del__(self):
        """析构函数，确保程序退出时关闭SSH连接"""
        self.close_ssh_session()

    def cmd_on_prefix_select(self, event):
        """处理cmd_prefix-list选择事件"""
        selected_indices = self.cmd_cmd_name_listbox.curselection()
        if selected_indices:
            self.cmd_selected_prefix = self.cmd_cmd_name_listbox.get(selected_indices[0])
            selected_command = self.cmd_selected_prefix
            self.status_var.set(f"选择的命令: {selected_command}")
            self.cmd_custom_command_entry.delete(0, tk.END)

    def line_on_prefix_select(self, event):
        """处理prefix-list选择事件"""
        selected_indices = self.line_name_listbox.curselection()
        if selected_indices:
            # 清除之前的选择状态
            self.line_ip_listbox.selection_clear(0, tk.END)

            # 获取新选择
            self.line_selected_prefix = self.line_name_listbox.get(selected_indices[0])
            self.line_update_ip_list()

    def outside_fetch_prefix_list_config(self):
        """Fetch current term configuration (route)"""
        if not self.validate_input():
            return

        command = "show configuration firewall filter inside-outside-fbf|display set |no-more "
        self.current_callback = self.outside_process_prefix_list_config
        self.start_query(command)

    def outside_process_prefix_list_config(self, output):
        """Process term configuration (route)"""
        try:
            pattern = r"set firewall filter inside-outside-fbf term (\S+) from source-address (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})"
            self.outside_prefix_list_dict = {}
            self.outside_selected_prefix = None
            self.outside_selected_ips = []

            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                match = re.match(pattern, line)
                if match:
                    term_name = match.group(1)
                    source_address = match.group(2)
                    if term_name in self.outside_prefix_list_dict:
                        if source_address not in self.outside_prefix_list_dict[term_name]:
                            self.outside_prefix_list_dict[term_name].append(source_address)
                    else:
                        self.outside_prefix_list_dict[term_name] = [source_address]

            self.outside_update_prefix_list_ui()

        except Exception as e:
            messagebox.showerror("错误", f"处理配置时出错：{str(e)}")

    def outside_update_prefix_list_ui(self):
        """Update term UI (route)"""
        self.outside_name_listbox.delete(0, tk.END)
        self.outside_ip_listbox.delete(0, tk.END)

        if not self.outside_prefix_list_dict:
            return

        sorted_names = sorted(self.outside_prefix_list_dict.keys())
        for name in sorted_names:
            self.outside_name_listbox.insert(tk.END, name)

        if sorted_names:
            self.outside_name_listbox.selection_set(0)
            self.outside_selected_prefix = sorted_names[0]
            self.outside_update_ip_list()

    def outside_update_ip_list(self):
        """Update IP list (route)"""
        self.outside_ip_listbox.delete(0, tk.END)
        self.outside_selected_ips = []

        if self.outside_selected_prefix and self.outside_selected_prefix in self.outside_prefix_list_dict:
            ips = sorted(self.outside_prefix_list_dict[self.outside_selected_prefix])
            for ip in ips:
                self.outside_ip_listbox.insert(tk.END, ip)

    def outside_on_prefix_select(self, event):
        """Handle prefix selection (route)"""
        selected_indices = self.outside_name_listbox.curselection()
        if selected_indices:
            self.outside_selected_prefix = self.outside_name_listbox.get(selected_indices[0])
            self.outside_update_ip_list()

    def outside_on_ip_select(self, event):
        """Handle IP selection (route)"""
        self.outside_selected_ips = [self.outside_ip_listbox.get(i) for i in self.outside_ip_listbox.curselection()]

    def outside_delete_selected_ips(self):
        """Delete selected IPs (route)"""
        if not self.outside_selected_prefix or not self.outside_selected_ips:
            messagebox.showwarning("警告", "请先选择要删除的IP地址")
            return

        remaining_ips = [ip for ip in self.outside_prefix_list_dict[self.outside_selected_prefix]
                         if ip not in self.outside_selected_ips]

        if not remaining_ips:
            messagebox.showwarning("警告", "每个term必须至少保留一个IP地址")
            return

        commands = []
        for ip in self.outside_selected_ips:
            if ip != "1.1.1.1/32":
                cmd = f"delete  firewall filter inside-outside-fbf term  {self.outside_selected_prefix} from source-address  {ip}"
                commands.append(cmd)

        if not commands:
            messagebox.showinfo("提示", "没有有效的IP地址需要删除")
            return

        self.outside_display_commands(commands)
        self.execute_config_commands(commands)
        self.root.after(3000, lambda: self.outside_refresh_prefix_list())

    def outside_add_new_ips(self):
        """Add new IPs (route)"""
        if not self.outside_selected_prefix:
            messagebox.showwarning("警告", "请先选择term")
            return

        new_ips = self.outside_ip_text.get("1.0", tk.END).strip().splitlines()
        ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$"
        valid_ips = [ip for ip in new_ips if re.match(ip_pattern, ip.strip())]

        if not valid_ips:
            messagebox.showwarning("警告", "无效的IP地址格式")
            return

        commands = [f"set firewall filter inside-outside-fbf  term {self.outside_selected_prefix} from source-address {ip}" for ip in valid_ips]
        self.outside_display_commands(commands)
        self.execute_config_commands(commands)
        self.outside_ip_text.delete("1.0", tk.END)
        self.root.after(3000, lambda: self.outside_refresh_prefix_list())

    def outside_refresh_prefix_list(self):
        """Refresh configuration (route)"""
        try:
            state = {
                'outside_selected_prefix': self.outside_selected_prefix,
                'outside_selected_ips': self.outside_selected_ips.copy(),
                'scroll_position': self.outside_name_listbox.yview(),
                'ip_scroll_position': self.outside_ip_listbox.yview()
            }
            self.status_var.set("正在刷新配置...")
            self.outside_fetch_prefix_list_config()
            self.root.after(1500, lambda: self.outside_restore_state_after_refresh(state))
        except Exception as e:
            self.status_var.set(f"刷新失败: {str(e)}")

    def outside_display_commands(self, commands):
        """Display commands (route)"""
        self.outside_cmd_output.config(state='normal')
        self.outside_cmd_output.insert(tk.END, "\n将要执行的命令:\n")
        for cmd in commands:
            self.outside_cmd_output.insert(tk.END, f"{cmd}\n")
        self.outside_cmd_output.see(tk.END)
        self.outside_cmd_output.config(state='disabled')

    def outside_commit_config_changes(self):
        """Commit configuration (route)"""
        commands = ["commit"]
        self.execute_config_commands(commands)


    def outside_restore_state_after_refresh(self, state):
        """从保存的状态恢复UI"""
        try:
            # 恢复选中状态
            if state['outside_selected_prefix'] and state['outside_selected_prefix'] in self.outside_prefix_list_dict:
                items = self.outside_name_listbox.get(0, tk.END)
                if state['outside_selected_prefix'] in items:
                    index = items.index(state['outside_selected_prefix'])
                    self.outside_name_listbox.selection_clear(0, tk.END)
                    self.outside_name_listbox.selection_set(index)
                    self.outside_name_listbox.see(index)
                    self.outside_on_prefix_select(None)



            # 恢复滚动位置
            self.outside_name_listbox.yview_moveto(state['scroll_position'][0])
            self.outside_ip_listbox.yview_moveto(state['ip_scroll_position'][0])

            self.status_var.set("配置已刷新")
        except Exception as e:
            self.bh_cmd_output.insert(tk.END, f"恢复状态时出错: {str(e)}")
            self.status_var.set("部分状态恢复失败")


    def route_fetch_prefix_list_config(self):
        """Fetch current router-list configuration (route)"""
        if not self.validate_input():
            return

        command = "show configuration routing-options static |display set |no-more "
        self.current_callback = self.route_process_prefix_list_config
        self.start_query(command)

    def route_process_prefix_list_config(self, output):
        """Process router-list configuration (route)"""
        try:
            pattern = r"""
            ^set\ routing-options\ static\ route\s+      # 固定开头
            (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})  # 目标IP段
            .*?                                         # 中间任意内容
            (?:next-hop|qualified-next-hop)\s+          # 两种下一跳类型
            (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})       # 下一跳IP
            """
            # 编译正则表达式（带注释模式）
            regex = re.compile(pattern, re.VERBOSE)
            self.route_prefix_list_dict = {}
            self.route_selected_prefix = None
            self.route_selected_ips = []

            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                match =regex.search(line)
                if match:
                    prefix_name = match.group(1)
                    ip_address = match.group(2)
                    if prefix_name in self.route_prefix_list_dict:
                        if ip_address not in self.route_prefix_list_dict[prefix_name]:
                            self.route_prefix_list_dict[prefix_name].append(ip_address)
                    else:
                        self.route_prefix_list_dict[prefix_name] = [ip_address]

            self.route_update_prefix_list_ui()

        except Exception as e:
            messagebox.showerror("错误", f"处理配置时出错：{str(e)}")

    def route_update_prefix_list_ui(self):
        """Update router-list UI (route)"""
        self.route_name_listbox.delete(0, tk.END)
        self.route_ip_listbox.delete(0, tk.END)

        if not self.route_prefix_list_dict:
            return

        sorted_names = sorted(self.route_prefix_list_dict.keys())
        for name in sorted_names:
            self.route_name_listbox.insert(tk.END, name)

        if sorted_names:
            self.route_name_listbox.selection_set(0)
            self.route_selected_prefix = sorted_names[0]
            self.route_update_ip_list()

    def route_update_ip_list(self):
        """Update IP list (route)"""
        self.route_ip_listbox.delete(0, tk.END)
        self.route_selected_ips = []

        if self.route_selected_prefix and self.route_selected_prefix in self.route_prefix_list_dict:
            ips = sorted(self.route_prefix_list_dict[self.route_selected_prefix])
            for ip in ips:
                self.route_ip_listbox.insert(tk.END, ip)

    def route_on_prefix_select(self, event):
        """Handle prefix selection (route)"""
        selected_indices = self.route_name_listbox.curselection()
        if selected_indices:
            self.route_selected_prefix = self.route_name_listbox.get(selected_indices[0])
            self.route_update_ip_list()

    def route_on_ip_select(self, event):
        """Handle IP selection (route)"""
        self.route_selected_ips = [self.route_ip_listbox.get(i) for i in self.route_ip_listbox.curselection()]

    def route_delete_selected_ips(self):
        """Delete selected IPs (route)"""
        if not self.route_selected_prefix :
            messagebox.showwarning("警告", "请先选择要删除的路由段")
            return


        commands = []
        cmd = f"delete  routing-options static route {self.route_selected_prefix} "
        commands.append(cmd)

        if not commands:
            messagebox.showinfo("提示", "没有有效的路由段需要删除")
            return

        self.route_display_commands(commands)
        self.execute_config_commands(commands)
        self.root.after(3000, lambda: self.route_refresh_prefix_list())

    def route_add_new_ips(self):

        new_ips = self.route_ip_text.get("1.0", tk.END).strip().splitlines()
        ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$"
        valid_ips = [ip for ip in new_ips if re.match(ip_pattern, ip.strip())]

        if not valid_ips:
            messagebox.showwarning("警告", "无效的路由段格式")
            return

        commands = []
        for ip in valid_ips:
            # 此处命令以实际为准，可以只有一条，也可以添加tag、as-path、community等静态路由属性。
            commands.append(f"set routing-options static route {ip} next-hop 192.168.1.1")
            commands.append(f"set routing-options static route {ip} tag 888")

        self.route_display_commands(commands)
        self.execute_config_commands(commands)
        self.route_ip_text.delete("1.0", tk.END)
        self.root.after(3000, lambda: self.route_refresh_prefix_list())

    def route_refresh_prefix_list(self):
        """Refresh configuration (route)"""
        try:
            state = {
                'route_selected_prefix': self.route_selected_prefix,
                'route_selected_ips': self.route_selected_ips.copy(),
                'scroll_position': self.route_name_listbox.yview(),
                'ip_scroll_position': self.route_ip_listbox.yview()
            }
            self.status_var.set("正在刷新配置...")
            self.route_fetch_prefix_list_config()
            self.root.after(1500, lambda: self.route_restore_state_after_refresh(state))
        except Exception as e:
            self.status_var.set(f"刷新失败: {str(e)}")

    def route_display_commands(self, commands):
        """Display commands (route)"""
        self.route_cmd_output.config(state='normal')
        self.route_cmd_output.insert(tk.END, "\n将要执行的命令:\n")
        for cmd in commands:
            self.route_cmd_output.insert(tk.END, f"{cmd}\n")
        self.route_cmd_output.see(tk.END)
        self.route_cmd_output.config(state='disabled')

    def route_commit_config_changes(self):
        """Commit configuration (route)"""
        commands = ["commit"]
        self.execute_config_commands(commands)


    def route_restore_state_after_refresh(self, state):
        """从保存的状态恢复UI"""
        try:
            # 恢复选中状态
            if state['route_selected_prefix'] and state['route_selected_prefix'] in self.route_prefix_list_dict:
                items = self.route_name_listbox.get(0, tk.END)
                if state['route_selected_prefix'] in items:
                    index = items.index(state['route_selected_prefix'])
                    self.route_name_listbox.selection_clear(0, tk.END)
                    self.route_name_listbox.selection_set(index)
                    self.route_name_listbox.see(index)
                    self.route_on_prefix_select(None)


            # 恢复滚动位置
            self.route_name_listbox.yview_moveto(state['scroll_position'][0])
            self.route_ip_listbox.yview_moveto(state['ip_scroll_position'][0])

            self.status_var.set("配置已刷新")
        except Exception as e:
            self.bh_cmd_output.insert(tk.END, f"恢复状态时出错: {str(e)}")
            self.status_var.set("部分状态恢复失败")

    def bh_fetch_prefix_list_config(self):
        """Fetch current BHr-list configuration (BH)"""
        if not self.validate_input():
            return

        command = 'show configuration routing-options|display set |match "discard"|no-more '
        self.current_callback = self.bh_process_prefix_list_config
        self.start_query(command)

    def bh_process_prefix_list_config(self, output):
        """Process BHr-list configuration (BH)"""
        try:
            pattern = r"set routing-options static route (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}) (discard)"
            self.bh_prefix_list_dict = {}
            self.bh_selected_prefix = None
            self.bh_selected_ips = []

            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                match = re.match(pattern, line)
                if match:
                    prefix_name = match.group(1)
                    ip_address = match.group(2)
                    if prefix_name in self.bh_prefix_list_dict:
                        if ip_address not in self.bh_prefix_list_dict[prefix_name]:
                            self.bh_prefix_list_dict[prefix_name].append(ip_address)
                    else:
                        self.bh_prefix_list_dict[prefix_name] = [ip_address]

            self.bh_update_prefix_list_ui()

        except Exception as e:
            messagebox.showerror("错误", f"处理配置时出错：{str(e)}")

    def bh_update_prefix_list_ui(self):
        """Update BHr-list UI (BH)"""
        self.bh_name_listbox.delete(0, tk.END)
        self.bh_ip_listbox.delete(0, tk.END)

        if not self.bh_prefix_list_dict:
            return

        sorted_names = sorted(self.bh_prefix_list_dict.keys())
        for name in sorted_names:
            self.bh_name_listbox.insert(tk.END, name)

        if sorted_names:
            self.bh_name_listbox.selection_set(0)
            self.bh_selected_prefix = sorted_names[0]
            self.bh_update_ip_list()

    def bh_update_ip_list(self):
        """Update IP list (BH)"""
        self.bh_ip_listbox.delete(0, tk.END)
        self.bh_selected_ips = []

        if self.bh_selected_prefix and self.bh_selected_prefix in self.bh_prefix_list_dict:
            ips = sorted(self.bh_prefix_list_dict[self.bh_selected_prefix])
            for ip in ips:
                self.bh_ip_listbox.insert(tk.END, ip)

    def bh_on_prefix_select(self, event):
        """Handle prefix selection (BH)"""
        selected_indices = self.bh_name_listbox.curselection()
        if selected_indices:
            self.bh_selected_prefix = self.bh_name_listbox.get(selected_indices[0])
            self.bh_update_ip_list()

    def bh_on_ip_select(self, event):
        """Handle IP selection (BH)"""
        self.bh_selected_ips = [self.bh_ip_listbox.get(i) for i in self.bh_ip_listbox.curselection()]

    def bh_delete_selected_ips(self):
        """Delete selected IPs (BH)"""
        if not self.bh_selected_prefix :
            messagebox.showwarning("警告", "请先选择要删除的路由段")
            return


        commands = []
        cmd = f"delete  routing-options static route  {self.bh_selected_prefix} "
        commands.append(cmd)

        if not commands:
            messagebox.showinfo("提示", "没有有效的路由段需要删除")
            return

        self.bh_display_commands(commands)
        self.execute_config_commands(commands)
        self.root.after(3000, lambda: self.bh_refresh_prefix_list())

    def bh_add_new_ips(self):

        new_ips = self.bh_ip_text.get("1.0", tk.END).strip().splitlines()
        ip_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$"
        valid_ips = [ip for ip in new_ips if re.match(ip_pattern, ip.strip())]

        commands = []
        if not valid_ips:
            messagebox.showwarning("警告", "无效的路由段格式")
            return
        for ip in valid_ips:
            if '/32' not in ip:
                messagebox.showwarning("警告", "只能是32位地址")
                return
            else:
                # 黑洞路由功能自定义，可以添加指定的tag、community等属性。
                commands.append(f"set routing-options static route {ip} discard")
                commands.append(f"set routing-options static route {ip} tag description")

        self.bh_display_commands(commands)
        self.execute_config_commands(commands)
        self.bh_ip_text.delete("1.0", tk.END)
        self.root.after(3000, lambda: self.bh_refresh_prefix_list())

    def bh_refresh_prefix_list(self):
        """Refresh configuration (BH)"""
        try:
            state = {
                'bh_selected_prefix': self.bh_selected_prefix,
                'bh_selected_ips': self.bh_selected_ips.copy(),
                'scroll_position': self.bh_name_listbox.yview(),
                'ip_scroll_position': self.bh_ip_listbox.yview()
            }
            self.status_var.set("正在刷新配置...")
            self.bh_fetch_prefix_list_config()
            self.root.after(1500, lambda: self.bh_restore_state_after_refresh(state))
        except Exception as e:
            self.status_var.set(f"刷新失败: {str(e)}")

    def bh_display_commands(self, commands):
        """Display commands (BH)"""
        self.bh_cmd_output.config(state='normal')
        self.bh_cmd_output.insert(tk.END, "\n将要执行的命令:\n")
        for cmd in commands:
            self.bh_cmd_output.insert(tk.END, f"{cmd}\n")
        self.bh_cmd_output.see(tk.END)
        self.bh_cmd_output.config(state='disabled')

    def bh_commit_config_changes(self):
        """Commit configuration (BH)"""
        commands = ["commit"]
        self.execute_config_commands(commands)


    def bh_restore_state_after_refresh(self, state):
        """从保存的状态恢复UI"""
        try:
            # 恢复选中状态
            if state['bh_selected_prefix'] and state['bh_selected_prefix'] in self.bh_prefix_list_dict:
                items = self.bh_name_listbox.get(0, tk.END)
                if state['bh_selected_prefix'] in items:
                    index = items.index(state['bh_selected_prefix'])
                    self.bh_name_listbox.selection_clear(0, tk.END)
                    self.bh_name_listbox.selection_set(index)
                    self.bh_name_listbox.see(index)
                    self.bh_on_prefix_select(None)


            # 恢复滚动位置
            self.bh_name_listbox.yview_moveto(state['scroll_position'][0])
            self.bh_ip_listbox.yview_moveto(state['ip_scroll_position'][0])

            self.status_var.set("配置已刷新")
        except Exception as e:
            self.bh_cmd_output.insert(tk.END, f"恢复状态时出错: {str(e)}")
            self.status_var.set("部分状态恢复失败")

if __name__ == "__main__":
    root = tk.Tk()
    app = JuniperRouteQueryApp(root)
    root.mainloop()