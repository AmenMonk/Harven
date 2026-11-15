// 哈文 SPA 路由 — 16 页施工表（总方案第 4 章）· B 批全量接线完成
// B15 协作进度卡 / B16 询问提示 为聊天内嵌组件，另设演示页
import { createRouter, createWebHashHistory } from 'vue-router'
import HomePage from '../pages/HomePage.vue'
import StatusPage from '../pages/StatusPage.vue'
import PanelsPage from '../pages/PanelsPage.vue'
import SpacePage from '../pages/SpacePage.vue'
import PanelShellPage from '../pages/PanelShellPage.vue'
import KnowledgePage from '../pages/KnowledgePage.vue'
import SkillsPage from '../pages/SkillsPage.vue'
import ButlerPage from '../pages/ButlerPage.vue'
import HubPage from '../pages/HubPage.vue'
import ModelsPage from '../pages/ModelsPage.vue'
import ModelInstallPage from '../pages/ModelInstallPage.vue'
import SettingsPage from '../pages/SettingsPage.vue'
import MorePage from '../pages/MorePage.vue'
import TimelinePage from '../pages/TimelinePage.vue'
import StudioPage from '../pages/StudioPage.vue'
import WelcomePage from '../pages/WelcomePage.vue'
import MailPage from '../pages/MailPage.vue'
import DemoCollabPage from '../pages/DemoCollabPage.vue'
import DemoAskPage from '../pages/DemoAskPage.vue'
import VerticalPage from '../pages/VerticalPage.vue'
import CreatePage from '../pages/CreatePage.vue'
import ProfilePage from '../pages/ProfilePage.vue'
import PhotosPage from '../pages/PhotosPage.vue'
import MemoryPage from '../pages/MemoryPage.vue'
import GoalsPage from '../pages/GoalsPage.vue'
import VisionPage from '../pages/VisionPage.vue'

// hash 模式：Flask 静态托管下无需后端路由配合，灰度期最稳
const routes = [
  { path: '/', name: 'home', component: HomePage, meta: { id: 'B1', title: '首页三栏', note: '聊天+会话+右栏停靠；＋新对话第一按钮；会话带模式/面板标签；防弄混四保险；左栏 7 格可拖拽排序；输入栏 ＋菜单+发送' } },
  { path: '/panels', name: 'panels', component: PanelsPage, meta: { id: 'B2', title: '面板选择器', note: '分类卡进空间页 + 模板库 + 已装面板卡墙 + Agent 卡片墙详情 + 待审批授权' } },
  { path: '/space/:cat', name: 'space', component: SpacePage, meta: { id: 'B2s', title: '分类空间页', note: '★ 大改版：家庭/公司/个人三套同构——时间轴+时间筛选+我的模块(可拖安装)+模块库+新手指导' } },
  { path: '/panel/:id', name: 'panel-shell', component: PanelShellPage, meta: { id: 'B3', title: '面板通用壳', note: '停靠/浮窗（可拖可缩）/全屏三形态' } },
  { path: '/knowledge', name: 'knowledge', component: KnowledgePage, meta: { id: 'B4', title: '知识库', note: '文档卡墙 + 过滤搜索 + 查看/删除 + 上传' } },
  { path: '/skills', name: 'skills', component: SkillsPage, meta: { id: 'B5', title: '技能库', note: '脚本技能卡墙 + 动作运行表单 + Hub 装新技能' } },
  { path: '/settings', name: 'settings', component: SettingsPage, meta: { id: 'B6', title: '设置单页', note: '五锚点（模型/安全隐私/通用/钥匙串/关于）；安全模式切换真实可用' } },
  { path: '/status', name: 'status', component: StatusPage, meta: { id: 'B7', title: '运行状态', note: '六卡含 token 账本，对接 /api/status/monitor + /api/token/ledger' } },
  { path: '/models', name: 'models', component: ModelsPage, meta: { id: 'B8', title: '模型中心', note: '三分区（外脑 8 预置/本地通用/本地垂直）+ 钥匙保存 + 切换' } },
  { path: '/models/install', name: 'model-install', component: ModelInstallPage, meta: { id: 'B9', title: '模型安装', note: '搜索制零推荐 + 硬件适配自动标注（绿/黄/红）' } },
  { path: '/vision', name: 'vision', component: VisionPage, meta: { id: 'B8v', title: '视觉引擎', note: '★ #1278 v1.3 融合：底座（yolox+clip）一键安装 + 理解大脑钉选（外脑/本地 GGUF）+ 模型导入删除' } },
  { path: '/more', name: 'more', component: MorePage, meta: { id: 'B10', title: '更多', note: '垂直模型/画像/插件中心/帮助' } },
  { path: '/vertical', name: 'vertical', component: VerticalPage, meta: { id: 'B17', title: '垂直大脑', note: '★ 反馈⑩：垂直模型直答入口——模型卡墙+表单直答+能力边界+图片/音频/文本结果' } },
  { path: '/create', name: 'create', component: CreatePage, meta: { id: 'C1', title: '创造区', note: '★ 2026-09-17 #737：造Agent（工坊搬迁）/ 造Skill（AI生成）/ 改哈文（受控进化提案审批中心）' } },
  { path: '/profile', name: 'profile', component: ProfilePage, meta: { id: 'C2', title: '画像管理', note: '★ 2026-09-18 #743：分维度管理（来源徽标+置信度+改删）/ 时间线 / 洞察与建议审批' } },
  { path: '/photos', name: 'photos', component: PhotosPage, meta: { id: 'C3', title: '图片库', note: '★ #916 统一图库：全模块照片统一索引+相册栏+缩略图墙+搜索' } },
  { path: '/memory', name: 'memory', component: MemoryPage, meta: { id: 'C4', title: '记忆相册', note: '★ #925 T4：手机生活照 AI 相册——相册墙+时间线+一句话找照片+AI 修图+看图编故事' } },
  { path: '/timeline', name: 'timeline', component: TimelinePage, meta: { id: 'B11', title: '时间轴', note: '会话按日/按月两粒度' } },
  { path: '/studio', name: 'studio', component: StudioPage, meta: { id: 'B12', title: '创作工坊', note: '创作历史卡墙（vertical history）' } },
  { path: '/welcome', name: 'welcome', component: WelcomePage, meta: { id: 'B13', title: '欢迎+登录', note: '默认密码提示 + 硬件检测 + 入口' } },
  // ★ 2026-10-25 #1183/#1184 入口收纳：任务页并入管家中心，/tasks 重定向（老 URLs 不炸）
  { path: '/butler', name: 'butler', component: ButlerPage, meta: { id: 'D1', title: '管家中心', note: '★ #1183：待确认收纳盒+今日议程+任务+目标+随手记 五区一页；#1184 任务/随身记/浮球入口统一收进来' } },
  { path: '/tasks', redirect: '/butler' },
  { path: '/goals', name: 'goals', component: GoalsPage, meta: { id: 'C5', title: '目标', note: '★ 2026-10-20 第二批2.2：目标进度卡墙+三步设定向导+详情时间线+偏差卡三选' } },
  { path: '/mail', name: 'mail', component: MailPage, meta: { id: 'B14b', title: '邮箱', note: '配置向导 + 聊天入口说明（发送必过确认）' } },
  { path: '/hub', name: 'hub', component: HubPage, meta: { id: 'B14c', title: 'HarvenHub', note: '市场浏览/搜索/分类 + 一键下载安装' } },
  { path: '/demo/collab', name: 'demo-collab', component: DemoCollabPage, meta: { id: 'B15', title: '协作进度卡（演示）', note: '流水线进度卡跑完收起，答案带 🤝 标注' } },
  { path: '/demo/ask', name: 'demo-ask', component: DemoAskPage, meta: { id: 'B16', title: '询问提示组件（演示）', note: '三状态 + 四位置复用同一组件' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHashHistory('/static/spa/'),
  routes,
})

export default router
