package com.skeleton.home
import org.json.JSONObject
internal data class HomeHistoryPreview(val items:List<JSONObject>,val total:Int,val visible:Int)
internal fun homeHistoryResultLabel(found:Int,complete:Boolean,loading:Boolean,error:String):String=when{
    !complete&&error.isNotBlank()->"Каталог недоступний"
    !complete&&loading->"Завантаження · знайдено $found (неповний список)"
    !complete->"Каталог ще не завантажено"
    loading->"Оновлення · знайдено $found"
    error.isNotBlank()->"Кеш · знайдено $found"
    else->"Загалом/видимі · знайдено $found"
}
