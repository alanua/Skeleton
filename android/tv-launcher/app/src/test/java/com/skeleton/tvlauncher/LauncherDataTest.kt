package com.skeleton.tvlauncher
import org.junit.Assert.assertEquals
import org.junit.Test
class LauncherDataTest {
    @Test fun historyFiltersHidden(){
        val x=LauncherData.history("""{"items":[{"history_id":"1","title":"Live","hidden":false},{"history_id":"2","title":"Hidden","hidden":true}]}""")
        assertEquals(1,x.size);assertEquals("Live",x.first().title)
    }
    @Test fun channelsKeepIdentity(){
        val x=LauncherData.channels("""{"channels":[{"channel_id":"abcdef","display_name":"TV1","programme_title":"News"}]}""")
        assertEquals("abcdef",x.first().id);assertEquals("News",x.first().detail)
    }
}
