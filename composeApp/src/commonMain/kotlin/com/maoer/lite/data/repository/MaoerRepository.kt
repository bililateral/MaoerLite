package com.maoer.lite.data.repository

import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * 模拟后端 API 的 Mock 仓库。
 * 
 * 在真实应用程序中，这里会注入 Ktor HttpClient（或类似工具）
 * 从 REST/GraphQL 端点获取数据。
 * 目前，它在模拟延迟后返回硬编码的推荐音频列表。
 *
 * 额外说明：
 * - 这里做了简单的内存缓存（[cached]）+ 互斥锁（[mutex]），用于避免同一份列表在多个地方被重复“加载”。
 * - 后续接真实接口时，可以把这里替换为：网络请求 + 本地缓存（磁盘）+ Flow 数据流。
 */
class MaoerRepository {

    private val mutex = Mutex()
    private var cached: List<Audio>? = null

    suspend fun getRecommendAudios(): List<Audio> {
        return mutex.withLock {
            cached?.let { return it }

            delay(1000) // 模拟网络延迟
            val list = listOf(
                Audio(
                    "1",
                    "全职高手 第一季",
                    "729声工场",
                    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRZPOOnCE75EfjGCLp59U7zj9QXp0HvDngGtg&s?w=500&h=700",
                    "24:15",
                    "https://storage.googleapis.com/uamp/The_Kyoto_Connection_-_Wake_Up/01_-_Intro_-_The_Way_Of_Waking_Up_feat_Alan_Watts.mp3"
                ),
                Audio(
                    "2",
                    "魔道祖师 完结篇",
                    "北斗企鹅",
                    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRixv4LvuKM3EugUC2HpUPmBUDNl2nRIEz7Xg&s?w=500&h=700",
                    "32:10",
                    "https://storage.googleapis.com/uamp/Kai_Engel_-_Irsens_Tale/04_-_Moonlight_Reprise.mp3"
                ),
                Audio(
                    "3",
                    "某某 第一季",
                    "某某",
                    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQsrkY_MtynQdVeSY9OqycrgNoonCEaXM-rdw&s?w=500&h=700",
                    "18:45",
                    "https://storage.googleapis.com/automotive-media/Jazz_In_Paris.mp3"
                ),
                Audio(
                    "4",
                    "三体 广播剧",
                    "729声工场",
                    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQhKjzejnoLkIwwojLuBOMRtBFBNPopLXnGAQ&s?w=500&h=750",
                    "45:00",
                    "https://storage.googleapis.com/automotive-media/The_Messenger.mp3"
                ),
                Audio(
                    "5",
                    "将进酒",
                    "光合积木",
                    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTB9_ScX73Bwy2Xx8dMuKd5O03hTQxKGdpLzw&s?w=500&h=700",
                    "29:30",
                    "https://storage.googleapis.com/automotive-media/Talkies.mp3"
                ),
                Audio(
                    "6",
                    "毕业生入职指南",
                    "张三",
                    "https://img.tusij.com/tgs_assets/ips_templ_preview/af/c2/da/lg_2897427_1586161176_5e8ae618cb658.jpg%21w390?auth_key=1790899200-0-0-787d24aca9df6c23fec715c9e5d8a8a5",
                    "05:20",
                    "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
                )
            )
            cached = list
            list
        }
    }
}
