package com.skeleton.home

import kotlin.math.max
import kotlin.math.min

internal data class HomeYoutubeLayout(
    val contentWidth:Float,
    val contentHeight:Float,
    val posterWidth:Float,
    val posterHeight:Float,
    val titleHeight:Float,
    val seasonHeight:Float,
    val seekInfoHeight:Float,
    val panelHeight:Float,
    val dpadDiameter:Float,
    val stackedSatellites:Boolean,
    val scrollsVertically:Boolean,
    val scrollsHorizontally:Boolean,
)

/** Sizes are logical dp from the actual card constraints, after the outer UI/insets. */
internal fun homeYoutubeLayout(width:Float,height:Float,fontScale:Float,showSeek:Boolean,live:Boolean=false,sourceHeight:Float=0f):HomeYoutubeLayout{
    require(width.isFinite()&&height.isFinite()&&fontScale.isFinite())
    require(width>0f&&height>=0f&&fontScale>0f&&sourceHeight.isFinite()&&sourceHeight>=0f)
    val contentWidth=max(width,200f)
    val controlsWidth=contentWidth-24f
    val stacked=controlsWidth<248f
    val extra=if(stacked)112f else 16f
    val title=7f+20f*fontScale
    val season=18f*fontScale
    val seekInfo=if(showSeek)18f*fontScale else 0f
    val seek=if(showSeek)seekInfo+22f else if(live)34f else 0f
    val fixed=title+season+seek+54f+12f+sourceHeight
    val minimumPanel=150f+extra
    val preferredPoster=contentWidth*9f/16f
    val minimumPoster=min(72f,preferredPoster)
    val contentHeight=max(height,fixed+minimumPanel+minimumPoster)
    val poster=min(preferredPoster,contentHeight-fixed-minimumPanel)
    val panel=contentHeight-fixed-poster
    val diameter=min(min(302f,controlsWidth*.9f),panel-extra).coerceAtLeast(150f)
    return HomeYoutubeLayout(contentWidth,contentHeight,poster*16f/9f,poster,title,season,seekInfo,panel,diameter,stacked,contentHeight>height+.01f,contentWidth>width+.01f)
}

internal fun homeVideoMinimumHeight(fontScale:Float):Float{
    require(fontScale.isFinite()&&fontScale>0f)
    // A visible poster plus transport/seek/volume and scalable title/season rows.
    return 249f+54f*fontScale
}
