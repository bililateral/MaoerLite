# 🐱 MaoerLite (猫耳Lite)

![Kotlin](https://img.shields.io/badge/Kotlin-2.0-7F52FF?style=flat&logo=kotlin)
![Compose Multiplatform](https://img.shields.io/badge/Compose%20Multiplatform-1.6.11-4285F4?style=flat&logo=jetpackcompose)
![Platform](https://img.shields.io/badge/Platform-Android%20%7C%20iOS-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

**MaoerLite** 是一个基于 **Kotlin Multiplatform (KMP)** 和 **Compose Multiplatform (CMP)** 技术栈构建的跨平台音频社区客户端原型（Demo）。

本项目旨在验证使用**一套代码**同时构建 Android 与 iOS 原生应用的可行性，并实践了现代化的移动端架构模式。

---

## 📸 运行截图 (Screenshots)

<div align="center">
  <img src="composeApp/src/commonMain/kotlin/com/maoer/lite/snapshots/snapshot1.png" width="30%" />
  <img src="composeApp/src/commonMain/kotlin/com/maoer/lite/snapshots/snapshot2.png" width="30%" />
  <img src="composeApp/src/commonMain/kotlin/com/maoer/lite/snapshots/snapshot3.png" width="30%" />
</div>

> *注：截图展示了 Android/iOS 双端一致的 Material Design 3 风格 UI。*

---

## 🛠 技术栈 (Tech Stack)

本项目采用全栈 Kotlin 开发，使用了目前 KMP 生态中最主流的开源框架：

*   **核心语言**: [Kotlin 2.0](https://kotlinlang.org/) (K2 Compiler)
*   **UI 框架**: [Compose Multiplatform](https://www.jetbrains.com/lp/compose-multiplatform/) (Material Design 3)
*   **路由导航**: [Voyager](https://voyager.adriel.cafe/) (专为 KMP 设计的导航库)
*   **依赖注入**: [Koin](https://insert-koin.io/) (轻量级 Kotlin 原生注入框架)
*   **图片加载**: [Coil 3](https://coil-kt.github.io/coil/) (完全支持 KMP 的网络图片加载)
*   **网络请求**: [Ktor](https://ktor.io/) (异步 HTTP 客户端，已配置 JSON 序列化)
*   **异步编程**: Kotlin Coroutines & Flow (响应式数据流)
*   **架构模式**: Clean Architecture + MVI/MVVM (ScreenModel)

---

## ✨ 已实现功能

*   **跨平台架构**: Android 和 iOS 共享 **95%+** 的业务逻辑与 UI 代码。
*   **首页瀑布流**: 
    *   使用 `LazyVerticalStaggeredGrid` 实现双列交错布局。
    *   基于 `StateFlow` 的数据驱动 UI，包含加载状态 (Loading) 管理。
*   **全局播放管理器 (Global Player Manager)**:
    *   单例模式管理音频播放状态，确保首页与详情页状态实时同步。
    *   支持上一首/下一首切换，**播放结束后自动连续播放下一首**，循环播放列表。
    *   **持久化存储**: 应用重启后，自动恢复上次播放的音频和**精确播放进度**。
    *   后台模拟播放 (Ticker)，不依赖真实音频引擎即可验证 UI 逻辑。
*   **沉浸式详情页**:
    *   **视差滚动 (Parallax Scrolling)**: 背景封面随手指滑动产生视差位移效果。
    *   **全局控制**: 集成播放/暂停、上一首、下一首、进度条拖拽控制。
*   **底部播放条 (Bottom Player Bar)**:
    *   首页常驻悬浮播放条，实时显示当前曲目信息与简易进度。
    *   点击即可快速跳转至详情页。

---

## 🚧 待办事项 (To-Do)

*   [ ] 接入真实的音频流媒体服务 (ExoPlayer/AVPlayer)。
*   [ ] 详情页背景高斯模糊效果。
*   [ ] 真实的 API 接口对接 (目前使用 Mock 数据)。

---

## 🚀 快速开始

### 环境要求
*   JDK 17+
*   Android Studio Koala 或更高版本
*   Xcode 15+ (仅 iOS 开发需要)

### Android 运行
```bash
./gradlew :composeApp:installDebug
```

### iOS 运行
1.  使用 Xcode 打开 `iosApp/iosApp.xcworkspace`
2.  选择模拟器并点击 Run

---

## 📂 项目结构

```text
commonMain/kotlin/com/maoer/lite/
├── data/           
│   ├── local/      # 本地持久化 (KeyValueStorage)
│   ├── manager/    # 播放管理器 (PlayerManager)
│   ├── model/      # 数据模型
│   └── repository/ # 数据仓库 (Mock Data)
├── di/             # Koin 依赖注入模块 (AppModule)
├── ui/             # Compose UI 界面
│   ├── home/       # 首页 (StaggeredGrid + BottomPlayerBar)
│   ├── detail/     # 详情页 (Parallax Effect + Player Controls)
│   └── ...
├── App.kt          # 应用入口
└── Platform.kt     # 平台差异化接口
```

---

## 📝 说明

本项目为 **技术验证 Demo**，旨在展示 KMP 架构能力。
项目中展示的音频信息均为 Mock 数据，不包含真实版权内容。