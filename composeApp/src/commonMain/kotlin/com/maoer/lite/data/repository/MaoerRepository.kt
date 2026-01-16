package com.maoer.lite.data.repository

import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.delay

/**
 * 模拟后端 API 的 Mock 仓库。
 * 
 * 在真实应用程序中，这里会注入 Ktor HttpClient（或类似工具）
 * 从 REST/GraphQL 端点获取数据。
 * 目前，它在模拟延迟后返回硬编码的推荐音频列表。
 */
class MaoerRepository {

    suspend fun getRecommendAudios(): List<Audio> {
        delay(1000) // 模拟网络延迟
        return listOf(
            Audio("1", "全职高手 第一季", "729声工场", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRZPOOnCE75EfjGCLp59U7zj9QXp0HvDngGtg&s?w=500&h=700", "24:15"),
            Audio("2", "魔道祖师 完结篇", "北斗企鹅", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRixv4LvuKM3EugUC2HpUPmBUDNl2nRIEz7Xg&s?w=500&h=700", "32:10"),
            Audio("3", "某某 第一季", "某某", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQsrkY_MtynQdVeSY9OqycrgNoonCEaXM-rdw&s?w=500&h=700", "18:45"),
            Audio("4", "三体 广播剧", "729声工场", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQhKjzejnoLkIwwojLuBOMRtBFBNPopLXnGAQ&s?w=500&h=750", "45:00"),
            Audio("5", "将进酒", "光合积木", "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTB9_ScX73Bwy2Xx8dMuKd5O03hTQxKGdpLzw&s?w=500&h=700", "29:30"),
            Audio("6", "毕业生入职指南", "张三", "https://img.tusij.com/tgs_assets/ips_templ_preview/af/c2/da/lg_2897427_1586161176_5e8ae618cb658.jpg%21w390?auth_key=1790899200-0-0-787d24aca9df6c23fec715c9e5d8a8a5", "05:20")
        )
    }
}