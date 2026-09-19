package com.maoer.lite.data.podcast

/** Curated pronunciations for our fixed catalog avoid platform/version and polyphonic differences. */
object PodcastPronunciations {
    val titles = mapOf(
        "apple-1582119137" to "yan zhong hua shu",
        "apple-1498170617" to "zi wo jin hua lun",
        "apple-1455784513" to "ao tu dian bo",
        "apple-1668626930" to "ni da bo ke",
        "apple-1559695855" to "zhi xing xiao jiu guan",
        "apple-1671490972" to "zong heng si hai",
        "apple-1676099257" to "gao qian nv hai",
        "apple-1770713549" to "xi xi fu gao su",
        "apple-1592646595" to "bu kai wan xiao Jokes Aside",
        "apple-1603580035" to "fei hua lian pian",
        "apple-1615939013" to "ban na tie shang ye chen fu lu",
        "apple-520986449" to "pan ji Jenny gao su ni xue ying yu liao mei guo kai yan ying yu Podcast",
        "apple-1794422481" to "zi xun zao 7 dian",
        "apple-1726135306" to "zi xi shi STUDY ROOM",
        "apple-1552904790" to "shang ye jiu shi zhe yang",
        "apple-1715590582" to "qian jing lao shi de hui ke ting",
        "apple-1577896182" to "bu ba tian liao si",
        "apple-1512932915" to "lai dou lai le ting le zai zou",
        "apple-1834069371" to "luo yong hao de shi zi lu kou",
        "apple-1731784296" to "tian zhen bu tian zhen",
        "apple-1696511339" to "san ge huo qiang shou",
        "apple-1697309319" to "luo ri zhi hou",
        "apple-1581271335" to "wu ren zhi xiao",
        "apple-1813894143" to "liang bu zhi yao",
        "apple-1796650777" to "English Learning Podcast",
        "apple-1771401634" to "tan li ren",
        "apple-1575323064" to "zheng jing ba ba",
        "apple-1771248028" to "bu zhi sheng",
        "apple-1546599837" to "jiang si da",
        "apple-1813149436" to "BBC sui shen ying yu",
    )
    val categories = mapOf(
        "艺术" to "yi shu", "休闲" to "xiu xian", "喜剧" to "xi ju",
        "犯罪纪实" to "fan zui ji shi", "商务" to "shang wu", "自我完善" to "zi wo wan shan",
        "纪实" to "ji shi", "社会与文化" to "she hui yu wen hua", "幽默对谈" to "you mo dui tan",
        "即兴表演" to "ji xing biao yan", "创业" to "chuang ye", "语言学习" to "yu yan xue xi",
        "新闻" to "xin wen", "健身" to "jian shen", "投资" to "tou zi", "教育" to "jiao yu",
        "情感与人际关系" to "qing gan yu ren ji guan xi", "爱好" to "ai hao", "科技" to "ke ji",
    )
}

/** Build once for the catalog; no network, conversion or indexing during typing. */
class PodcastSearchIndex(sources: List<PodcastSource>) {
    private val entries = sources.map { source ->
        val title = PodcastPronunciations.titles[source.id].orEmpty()
        val category = PodcastPronunciations.categories[source.category].orEmpty()
        source to listOf(source.title, source.category, title, category, initials(title), initials(category))
            .map(::normalize).filter(String::isNotEmpty)
    }

    fun search(query: String): List<PodcastSource> {
        if (query.isBlank()) return entries.map { it.first }
        val compact = normalize(query)
        if (compact.isEmpty()) return emptyList()
        val terms = query.trim().split(Regex("\\s+")).map(::normalize).filter(String::isNotEmpty)
        return entries.filter { (_, fields) ->
            fields.any { compact in it } || terms.all { term -> fields.any { term in it } }
        }.map { it.first }
    }

    private fun initials(value: String) = value.split(' ').filter(String::isNotEmpty).joinToString("") { it.take(1) }

    private fun normalize(value: String): String = buildString {
        for (original in value.lowercase()) {
            val char = when (original) {
                in 'Ａ'..'Ｚ' -> original - 0xFEE0
                in 'ａ'..'ｚ' -> original - 0xFEE0
                in '０'..'９' -> original - 0xFEE0
                'ü', 'ǖ', 'ǘ', 'ǚ', 'ǜ' -> 'v'
                else -> original
            }
            if (char.isLetterOrDigit()) append(char.lowercaseChar())
        }
    }
}
