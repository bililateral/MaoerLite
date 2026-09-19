# MaoerLite · 猫耳

**听见，另一种生活。**

MaoerLite 是一款以公开 RSS 为内容入口的 Android 播客点播器。使用 Compose Multiplatform 构建界面、Kotlin Multiplatform 组织业务逻辑，提供杂志风首页、分类浏览、中文与拼音搜索，以及可保存收听进度的播放器。

当前版本：**1.1.0-dev**（versionCode 2）。内置目录包含 **30 个节目、19 个分类**，节目分集从真实 RSS 加载。本阶段交付 Android；iOS 保留工程入口及接口存根，尚未完成播放能力。

## 页面预览

以下 8 张截图来自当前开发版本的 Android API 36 模拟器。点击图片可查看原图；节目封面与标题来自对应内容源，分集数量和内容会随 RSS 更新。

| 杂志风首页 | 分类浏览 |
| --- | --- |
| <img src="docs/screenshots/home-two-pages-android-api36.png" alt="首页：大封面精选、节目列表，播放栏位于底部导航上方" width="320"> | <img src="docs/screenshots/categories-android-api36.png" alt="分类：左侧分类目录、右侧分组节目网格" width="320"> |
| 大封面精选与节目列表；首页不放分类标签。 | 左侧选择分类，右侧浏览节目；底部仅保留首页、分类两个入口。 |

| 节目与分集 | 大封面播放页 |
| --- | --- |
| <img src="docs/screenshots/podcast-library-android-api36.png" alt="岩中花述节目页：86 集、收藏与逐集收听进度" width="320"> | <img src="docs/screenshots/player-overview-android-api36.png" alt="播放页：大封面、分集标题、收藏与真实时长" width="320"> |
| 查看节目介绍和完整分集列表，保留每集的收听位置。 | 保留大封面布局；向下滚动可查看完整控制区和分集简介。 |

| 我的收藏 | 收听历史 |
| --- | --- |
| <img src="docs/screenshots/library-favorites-android-api36.png" alt="我的收听：节目收藏与分集收藏" width="320"> | <img src="docs/screenshots/library-history-android-api36.png" alt="我的收听：逐集历史与已保存进度" width="320"> |
| 节目和分集分别收藏，从首页右上角进入“我的收听”。 | 按分集保存收听记录，可续听或移除记录。 |

| 拼音搜索 | 完整播放队列 |
| --- | --- |
| <img src="docs/screenshots/home-pinyin-search-android-api36.png" alt="输入 yzhs 搜索到岩中花述" width="320"> | <img src="docs/screenshots/playback-queue-current-android-api36.png" alt="岩中花述完整 86 集播放队列" width="320"> |
| 输入 `yzhs` 即可找到“岩中花述”，无需切换中文输入法。 | 队列保留全部分集，上一集、下一集支持首尾循环。 |

## 功能说明

### 发现与分类

- **首页**：暖色背景、红色点缀、大封面精选和节目列表；支持直接搜索节目。
- **分类**：左侧分类目录与右侧分组节目网格联动，支持分类内浏览和搜索。
- **导航**：只有“首页”和“分类”两个主入口。迷你播放栏单独占位，放在导航上方，并预留系统手势区域。
- **RSS 内容**：加载节目介绍、封面和分集信息，支持缓存及失败提示。元数据缓存用于再次浏览，不代表音频已下载。
- **结果定位**：修改搜索条件后，结果从顶部展示；点击分类目录定位到对应分组。首页与分类页分别保留浏览位置。

### 中文与拼音搜索

首页和分类页使用同一套本地搜索索引。输入时匹配内置节目的名称、分类及对应拼音，不需要向服务器提交搜索词。

| 想找的内容 | 可以输入 |
| --- | --- |
| 岩中花述：中文 | `岩中花述`、`花述` |
| 岩中花述：全拼 | `yanzhonghuashu`、`yan zhong hua shu` |
| 岩中花述：首字母或部分拼音 | `yzhs`、`huashu` |
| 科技分类下的节目 | `科技`、`keji`、`kj` |

搜索忽略英文字母大小写，兼容全角字母、数字，并对空格及标点做归一化处理。多个用空格分开的关键词也可分别匹配节目名称和分类。清空搜索词后恢复目录列表。

拼音索引覆盖当前固定的 30 个节目与 19 个分类；它不是任意中文全文转拼音服务。“我的收听”也可按节目拼音查找相关记录；RSS 动态分集标题按原文匹配，不承诺任意分集标题都可用拼音检索。

### 播放与队列

- 基于 **AndroidX Media3** 播放网络音频，支持后台播放、媒体通知及系统媒体控制。
- 进度条使用实际播放位置与媒体时长，可拖动定位；不使用演示时长。
- 节目页按当前分集筛选和排序生成队列，未筛选时包含整个节目的分集，不受列表分批展示数量限制。未开启睡眠定时时，队列第一集点“上一集”进入最后一集，最后一集点“下一集”回到第一集。
- 支持 **0.75×～2.0×** 倍速；睡眠定时提供预设时长、自定义时长及本集播完停止。
- 点击迷你播放栏的标题或封面进入播放页；播放页可收藏、标记听完，并查看分集简介。
- 应用重启后恢复队列与收听信息，保持暂停，避免自动出声。

### 我的收听

点击首页右上角的资料库图标进入“我的收听”。这是二级页面，不增加第三个底部主入口。

| 页面 | 用途 |
| --- | --- |
| 继续收听 | 找回未听完的分集，从各自保存的位置继续 |
| 收藏 | 管理节目收藏、分集收藏 |
| 历史 | 查看逐集收听记录，支持移除记录 |
| 下载 | 查看本地下载任务与文件；当前内置节目尚未开放下载 |

进度以分集为单位保存：切换节目或分集不会用新位置覆盖上一集的位置。支持标记已听完，重新播放已完成的分集可从头开始。收藏、历史和进度保存在设备本地；当前没有账号登录、跨设备同步、用户订阅或自定义 RSS 导入。

### 下载能力与当前开放范围

Android 下载链路已接入系统 **DownloadManager**，实现任务状态展示、失败重试、取消、删除，以及已下载文件的本地播放。系统负责后台传输和网络条件调度。

**当前 30 个内置节目均未开放下载。** 来源核查中，两个声湃源已找到允许个人离线收听的附条件许可，但许可展示、来源链接、请求标识和缓存规则等接入条件尚待落实；其余 28 个源尚未确认具体适用的使用许可。技术链路验证通过不等于内容来源条件已全部满足。

## 技术组成

| 层次 | 实现 |
| --- | --- |
| 共享业务与界面 | Kotlin 2.0.0、Compose Multiplatform 1.6.11、Coroutines |
| 页面导航与依赖注入 | Voyager 1.0.0、Koin 3.5.6 |
| 网络与 RSS | Ktor 2.3.12、xmlutil 0.86.3 |
| 图片加载 | Coil 3.0.4 |
| Android 播放 | Media3 1.4.0、MediaLibraryService、MediaController |
| 本地保存 | DataStore 1.1.1、Okio 3.9.1、逐条 JSON 文件持久化 |
| Android 下载 | 系统 DownloadManager |
| 构建 | Gradle Wrapper 8.14.3、Android Gradle Plugin 8.5.2 |

共享层负责目录、RSS 解析、搜索、队列规则和收听资料；Android 层负责播放器、媒体服务与下载系统的接入。

```text
composeApp/src/
├── commonMain/kotlin/com/maoer/lite/
│   ├── data/podcast/       # 内置目录、RSS、缓存与拼音索引
│   ├── data/library/       # 收藏、历史、逐集进度与持久化
│   ├── data/manager/       # 播放状态、队列与定时规则
│   ├── data/download/      # 下载模型与接口
│   ├── ui/                 # 首页、分类、节目、播放和资料库页面
│   └── di/                 # 共享依赖配置
├── androidMain/
│   ├── AndroidManifest.xml # 应用入口、网络及前台播放权限、服务声明
│   ├── kotlin/com/maoer/lite/
│   │   ├── MainActivity.kt                   # Android Activity，承载共享 Compose 界面
│   │   ├── MaoerApplication.kt               # Application 初始化与 Koin 启动
│   │   ├── Platform.android.kt               # Android 平台信息实现
│   │   ├── data/
│   │   │   ├── manager/
│   │   │   │   └── AndroidMediaPlayerController.kt # 连接媒体服务、控制播放、同步状态
│   │   │   ├── download/
│   │   │   │   └── AndroidDownloads.kt       # 系统下载任务、状态恢复与本地文件
│   │   │   └── local/
│   │   │       └── DataStoreConfig.android.kt # 应用私有目录中的 DataStore 路径
│   │   ├── service/
│   │   │   ├── MaoerPlaybackService.kt       # ExoPlayer、媒体会话、后台播放与进度保存
│   │   │   └── PlaybackCommands.kt           # 睡眠定时命令与会话状态字段
│   │   └── di/
│   │       └── PlatformModule.android.kt    # 注入 Android 播放器和下载实现
│   └── res/                                 # Android 启动图标、应用名称等资源
└── iosMain/                # iOS 工程入口与平台接口存根
gradle/libs.versions.toml   # 依赖版本
iosApp/                    # iOS 宿主工程
```

### Android 层如何协作

| 组件 | 具体职责 |
| --- | --- |
| `MainActivity` / `MaoerApplication` | 启动 Android 应用，初始化依赖注入，并把共享 Compose 页面挂载到 Activity。 |
| `AndroidMediaPlayerController` | 实现共享层的播放器接口，通过 MediaController 连接后台服务；发送播放、暂停、定位、倍速和定时命令，将播放状态与实际进度反馈给界面。 |
| `MaoerPlaybackService` | 继承 MediaLibraryService，持有 ExoPlayer 和媒体会话；处理后台播放、系统媒体控制、队列切换和睡眠定时，并保存收听进度及播放快照。 |
| `PlaybackCommands` | 统一控制器与服务之间的自定义命令和字段，包括定时分钟数、剩余时间及本集结束停止状态。 |
| `AndroidDownloads` | 把共享下载接口接到系统 DownloadManager，管理任务入队、状态查询、重试、取消、删除及任务恢复，并提供可播放的本地文件。 |
| `DataStoreConfig.android` | 提供应用私有 `filesDir` 下的 DataStore 文件路径。收藏、历史等逐条 JSON 持久化逻辑位于共享层 `data/library`。 |
| `PlatformModule.android` | 通过 Koin 将共享的 `MediaPlayerController`、`Downloads` 接口绑定到 Android 实现。 |

播放命令从共享页面经过 `PlayerManager` 和 `AndroidMediaPlayerController` 到达后台媒体服务，播放状态再回传到界面。下载传输由系统 DownloadManager 执行，下载模块提供状态和文件供资料库、播放器使用。

## 构建与运行

### 环境准备

| 项目 | 要求或当前配置 |
| --- | --- |
| JDK | 17 或以上；本地验证使用 JDK 21 |
| Android SDK | 安装 Platform 34 和 Build Tools；AGP 默认需要 Build Tools 34.0.0 |
| Android 版本 | 最低 Android 7.0 / API 24，compileSdk 与 targetSdk 均为 34 |
| 运行设备 | Android 真机或模拟器；本地功能验收设备为 API 36 模拟器 |
| Gradle | 使用仓库自带 Wrapper；首次构建需下载依赖 |

在仓库根目录创建本机 `local.properties`，填写 SDK 路径，例如：

```properties
sdk.dir=G:/AndroidSDK
```

现有 `gradle.properties` 中设置了本机 JDK 路径。其他机器需要改为实际路径，或在命令行用 `-Dorg.gradle.java.home=...` 覆盖；不要直接沿用不存在的目录。

### 生成 Debug APK

在仓库根目录运行：

```powershell
.\gradlew.bat :composeApp:assembleDebug
```

如需覆盖 JDK 路径：

```powershell
.\gradlew.bat '-Dorg.gradle.java.home=C:/Program Files/Java/jdk-21' :composeApp:assembleDebug
```

macOS / Linux 对应命令为 `./gradlew :composeApp:assembleDebug`。

生成的安装包位于：

```text
composeApp/build/outputs/apk/debug/composeApp-debug.apk
```

连接已开启 USB 调试的设备，或先启动 Android 模拟器，再安装：

```powershell
.\gradlew.bat :composeApp:installDebug
```

应用包名为 `com.maoer.lite`。运行时需要联网加载未缓存的 RSS、封面与音频。业务构建不依赖本地维护工具或验收脚本。

## 当前验证状态

以下为 **2026-09-19** 的本地验证结果，基于开发候选版本，不等同于正式发布验收完成。

| 项目 | 结果与边界 |
| --- | --- |
| 构建 | Android Debug APK 构建通过 |
| 单元与内容检查 | 39 项 JVM 测试通过，覆盖真实 RSS 等业务逻辑；6 项内容审计测试通过 |
| 页面与搜索 | API 36 验证首页、分类、中文/拼音检索、滚动复位与 1.3 倍字体布局 |
| 播放 | 30 个节目各抽取一集，实际解码、播放与定位通过 |
| 队列 | 岩中花述完整 86 集，首集向前到第 86 集、末集向后回到第 1 集通过 |
| 收听资料 | 收藏、独立分集进度、历史、完成状态、重听和重启恢复通过 |
| 下载自动调度 | 条件通过：在 Android 真实判定联网有效的网络上自然调度，未强制运行任务；本地音频样本的失败、重试、完成、离线播放及清理链路通过 |
| 来源使用条件 | 尚未全部通过，当前下载开关全部关闭 |

默认模拟器网络中，Google HTTPS 联网探测不可达，可能导致下载任务等待网络。验证时临时换用可达的真实探测端点，完成后恢复原设置；这不代表默认网络问题已修复，也不代表长音频、跨日后台运行或所有真机环境均已验证。

后续仍需完成来源接入条件、跨日稳定性与真机专项。iOS 播放、账号体系、跨设备同步及自定义 RSS 导入不在当前交付范围。

## 文档与维护约定

README 和本页引用的 8 张当前截图随项目提交。开发过程中的 BUG 原因、修复方法与验证记录持续更新；新增维护文档、回归测试、内容核查资料及辅助工具按维护者要求仅本地保留，不纳入业务提交。因此，上述测试数量描述本地实际执行结果，不能视为当前提交附带了完整验收套件。

更新界面后需同步截图和说明，清理失效链接及被替代的预览图。节目封面、标题、简介与音频属于各自的内容来源；本项目不把公开 RSS 可访问视为任意转载或离线分发授权。
