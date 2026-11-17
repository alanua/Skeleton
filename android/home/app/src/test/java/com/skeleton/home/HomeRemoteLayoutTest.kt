package com.skeleton.home

import org.junit.Assert.*
import org.junit.Test

class HomeRemoteLayoutTest{
    @Test fun allRepresentativeWindowsKeepControlsUsableAndPostersProportional(){
        val windows=listOf(320f to 568f,360f to 640f,393f to 873f,412f to 915f,700f to 180f,240f to 150f,160f to 100f)
        for((width,height)in windows)for(scale in listOf(1f,1.3f,1.5f,2f))for(seek in listOf(false,true)){
            val p=homeYoutubeLayout(width,height,scale,seek)
            assertTrue(p.dpadDiameter*.32f>=48f-.01f)
            assertTrue(p.dpadDiameter<=p.contentWidth-24f+.01f)
            assertTrue(p.posterWidth<=p.contentWidth+.01f)
            assertEquals(16f/9f,p.posterWidth/p.posterHeight,.001f)
            assertTrue(p.dpadDiameter+(if(p.stackedSatellites)112f else 16f)<=p.panelHeight+.01f)
            assertTrue(p.contentHeight>=height)
            assertEquals(p.contentHeight>height+.01f,p.scrollsVertically)
            assertEquals(p.contentWidth>width+.01f,p.scrollsHorizontally)
            val seekHeight=if(seek)p.seekInfoHeight+22f else 0f
            assertEquals(p.contentHeight,p.posterHeight+p.titleHeight+p.seasonHeight+seekHeight+p.panelHeight+66f,.02f)
        }
    }
    @Test fun shortWindowsScrollInsteadOfClippingTouchTargets(){
        val p=homeYoutubeLayout(393f,180f,1.5f,true)
        assertTrue(p.scrollsVertically);assertEquals(150f,p.dpadDiameter,.01f)
    }
    @Test fun largeWindowUsesFullWidthPosterAndCapsDpad(){
        val p=homeYoutubeLayout(412f,915f,1f,true)
        assertFalse(p.scrollsVertically);assertEquals(412f,p.posterWidth,.01f)
        assertEquals(302f,p.dpadDiameter,.01f)
    }
    @Test fun portraitPosterAndVideoFooterGetMoreSpaceWithLargeFont(){
        assertTrue(homeVideoMinimumHeight(1.5f)>homeVideoMinimumHeight(1f))
        assertTrue(homeVideoMinimumHeight(1f)>300f)
    }
    @Test fun liveStreamReservesStatusWithoutInventingASeekBar(){
        val p=homeYoutubeLayout(393f,600f,1.3f,false,true)
        assertEquals(0f,p.seekInfoHeight,0f)
        assertEquals(p.contentHeight,p.posterHeight+p.titleHeight+p.seasonHeight+34f+p.panelHeight+66f,.02f)
    }
    @Test fun invalidGeometryIsRejected(){
        assertThrows(IllegalArgumentException::class.java){homeYoutubeLayout(Float.NaN,500f,1f,true)}
        assertThrows(IllegalArgumentException::class.java){homeYoutubeLayout(393f,500f,0f,true)}
    }
}
