package com.skeleton.tvlauncher
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
private val Bg=Color(0xFF101722)
private val Panel=Color(0xFF1E2A39)
private val TextColor=Color(0xFFF1F3F7)
private val MutedColor=Color(0xFFBCC7D6)
private val Highlight=Color(0xFFEDA842)
private enum class Page{HOME,VIDEO,TV}
class MainActivity:ComponentActivity(){
  override fun onCreate(savedInstanceState:Bundle?){
    super.onCreate(savedInstanceState)
    setContent{MaterialTheme{LauncherScreen { openYoutube() }}}
  }
  private fun openYoutube(){
    for(pkg in listOf("com.google.android.youtube.tv","com.google.android.youtube")){
      val intent=packageManager.getLaunchIntentForPackage(pkg)?:continue
      startActivity(intent);return
    }
    runCatching{startActivity(Intent(Intent.ACTION_VIEW,Uri.parse("https://www.youtube.com/tv")))}
  }
}
@Composable private fun LauncherScreen(openYoutube:()->Unit){
  var page by remember{mutableStateOf(Page.HOME)}
  var entries by remember{mutableStateOf<List<CatalogItem>>(emptyList())}
  var selected by remember{mutableStateOf<CatalogItem?>(null)}
  var loading by remember{mutableStateOf(false)}
  var error by remember{mutableStateOf("")}
  val focus=remember(page){FocusRequester()}
  BackHandler(page!=Page.HOME){page=Page.HOME}
  LaunchedEffect(page){
    selected=null;entries=emptyList();error=""
    val route=when(page){Page.VIDEO->LauncherData.HISTORY;Page.TV->LauncherData.CHANNELS;else->null}
    if(route!=null){
      loading=true
      try{
        val response=withContext(Dispatchers.IO){LauncherData.read(route)}
        entries=if(page==Page.VIDEO)LauncherData.history(response) else LauncherData.channels(response)
      }catch(e:Exception){error="Home Edge недоступний: "+e.javaClass.simpleName}
      finally{loading=false}
    }
  }
  LaunchedEffect(page,loading){if(!loading)runCatching{focus.requestFocus()}}
  Column(Modifier.fillMaxSize().background(Bg).padding(horizontal=48.dp,vertical=30.dp),
    verticalArrangement=Arrangement.spacedBy(20.dp)){
    Row(verticalAlignment=Alignment.CenterVertically){
      Text("SKELETON TV",fontSize=31.sp,fontWeight=FontWeight.Bold,color=TextColor)
      Spacer(Modifier.weight(1f))
      Text("Home Edge · спільний каталог",fontSize=15.sp,color=MutedColor)
    }
    if(page==Page.HOME){
      Text("YouTube · Відео · ТВ",color=MutedColor,fontSize=18.sp)
      Row(horizontalArrangement=Arrangement.spacedBy(18.dp)){
        Tile("YouTube","Офіційний застосунок",Modifier.weight(1f).focusRequester(focus)){openYoutube()}
        Tile("Відео","Фільми, серіали та історія",Modifier.weight(1f)){page=Page.VIDEO}
        Tile("ТВ","Канали IPTV",Modifier.weight(1f)){page=Page.TV}
      }
    }else{
      Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(18.dp)){
        Tile("← Назад","Головна",Modifier.width(170.dp).focusRequester(focus)){page=Page.HOME}
        Text(if(page==Page.VIDEO)"Відео" else "ТВ",fontSize=27.sp,color=TextColor)
        Spacer(Modifier.weight(1f))
        Text(entries.size.toString()+" записів",fontSize=14.sp,color=MutedColor)
      }
      if(loading)LinearProgressIndicator(Modifier.fillMaxWidth())
      if(error.isNotEmpty())Text(error,color=Highlight)
      Row(horizontalArrangement=Arrangement.spacedBy(18.dp)){
        LazyColumn(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(8.dp)){
          items(entries,key={it.id}){item->
            var focused by remember{mutableStateOf(false)}
            Column(Modifier.fillMaxWidth().onFocusChanged{focused=it.isFocused}
              .border(if(focused)3.dp else 1.dp,if(focused)Highlight else Panel,RoundedCornerShape(12.dp))
              .background(if(selected?.id==item.id)Color(0xFF354558) else Panel,RoundedCornerShape(12.dp))
              .clickable{selected=item}.focusable().padding(horizontal=14.dp,vertical=12.dp)){
              Text(item.title,color=TextColor,fontSize=18.sp,maxLines=2,overflow=TextOverflow.Ellipsis)
              if(item.detail.isNotEmpty())Text(item.detail,color=MutedColor,fontSize=13.sp)
            }
          }
        }
        Column(Modifier.width(342.dp).background(Panel,RoundedCornerShape(14.dp)).padding(20.dp),
          verticalArrangement=Arrangement.spacedBy(12.dp)){
          Text(selected?.title?:"Оберіть позицію",color=TextColor,fontSize=22.sp,fontWeight=FontWeight.Bold)
          if(selected?.detail?.isNotEmpty()==true)Text(selected!!.detail,color=MutedColor)
          Text(if(page==Page.VIDEO)"Історія зі спільної бази Skeleton. Пряме відтворення на TV ще не активоване."
            else "Список IPTV з Home Edge. Пряме відтворення потребує нового TV-endpoint.",
            fontSize=14.sp,color=MutedColor,lineHeight=20.sp)
          Text("Команди на Sharp не надсилаються.",color=Highlight,fontSize=13.sp)
        }
      }
    }
    Spacer(Modifier.weight(1f))
    Text("D-pad: стрілки, OK, Back. Home APK: після прив'язки Android TV до Skeleton.",
      color=MutedColor,fontSize=13.sp)
  }
}
@Composable private fun Tile(title:String,subtitle:String,modifier:Modifier=Modifier,onClick:()->Unit){
  var focused by remember{mutableStateOf(false)}
  Column(modifier.onFocusChanged{focused=it.isFocused}
    .border(if(focused)3.dp else 1.dp,if(focused)Highlight else Color(0xFF364758),RoundedCornerShape(15.dp))
    .background(Panel,RoundedCornerShape(15.dp)).clickable(onClick=onClick).focusable()
    .padding(horizontal=18.dp,vertical=22.dp),verticalArrangement=Arrangement.spacedBy(8.dp)){
    Text(title,fontSize=23.sp,fontWeight=FontWeight.Bold,color=TextColor)
    Text(subtitle,fontSize=14.sp,color=MutedColor,maxLines=2)
  }
}
