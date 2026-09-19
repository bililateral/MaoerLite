# MaoerLite

使用 Compose Multiplatform 构建 UI、Kotlin Multiplatform 编写业务逻辑的 Android 播客点播器，当前开发版本为 **1.1.0-dev**。

## 功能

- 内置 30 个真实公开 RSS 节目，支持节目与分集浏览、缓存、搜索及顺序切换。
- 杂志风首页和左右联动分类页；底部只有两个主入口，播放栏独立位于导航上方。
- 节目搜索支持中文、全拼和首字母，例如 `岩中花述`、`yanzhonghuashu`、`yzhs`。动态分集标题按原文匹配。
- Media3 网络与后台播放、真实时长、定位、完整队列首尾循环、倍速和睡眠定时。
- “我的收听”提供节目/分集收藏、历史、逐集续听、已听完标记及本地搜索；重启恢复时保持暂停。
- 下载管理支持系统后台传输、重试、取消、删除与本地播放，按来源条件控制是否开放。

没有账号、用户订阅、RSS 导入或跨设备同步。本阶段只交付 Android；iOS 保留工程入口及接口存根。

## 当前界面

| 首页 | 分类 |
| --- | --- |
| ![当前首页](docs/screenshots/home-two-pages-android-api36.png) | ![当前分类](docs/screenshots/categories-android-api36.png) |

## 验证范围

本地 API 36 模拟器已验证双页搜索、86 集循环、收藏、逐集续听、完成状态及大字体布局；30 个节目各一集通过实际解码、播放和定位。系统自动下载调度在真实通过 Android 联网验证的网络上通过。默认模拟器网络的 Google HTTPS 探测不可达，仍可能导致任务等待网络。

当前目录是开发候选，**下载均未开放**：两个声湃源已找到允许个人离线收听的附条件许可，接入条件尚待落实；其余 28 个源的具体使用许可仍未确认。来源核查、跨日稳定性和真机专项未全部完成，不能作为正式发布验收通过。

## 构建

使用 JDK 17 或以上、Android SDK Platform 34 及所需 Build Tools。在仓库根目录的本机 `local.properties` 设置 `sdk.dir`。现有 `gradle.properties` 包含本机 JDK 路径，其他环境可通过 `-Dorg.gradle.java.home=实际路径` 覆盖。

```powershell
.\gradlew.bat :composeApp:assembleDebug
```

APK：`composeApp/build/outputs/apk/debug/composeApp-debug.apk`。

维护文档、回归测试、内容核查记录与辅助工具按维护者要求仅在本地保留，不包含在业务提交中；上面的验收结论描述本地已执行结果。
