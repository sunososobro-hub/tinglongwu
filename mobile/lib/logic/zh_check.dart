/// Heuristic Traditional-Chinese check (pure logic).
///
/// Qwen tends to answer in Simplified Chinese. We flag output that contains
/// characters which exist only in Simplified (high-frequency set below), or
/// that still contains Cyrillic (translation failed). Heuristic only: a text
/// made purely of characters shared by both scripts passes.
library;

class ZhCheck {
  const ZhCheck({
    required this.cjkCount,
    required this.simplifiedCount,
    required this.cyrillicCount,
  });

  final int cjkCount;
  final int simplifiedCount;
  final int cyrillicCount;

  bool get hasChinese => cjkCount > 0;
  bool get hasCyrillic => cyrillicCount > 0;

  /// OK = has Chinese, no Simplified-only characters, no Cyrillic leakage.
  bool get isTraditional =>
      hasChinese && simplifiedCount == 0 && cyrillicCount == 0;

  @override
  String toString() =>
      'ZhCheck(cjk=$cjkCount, simplified=$simplifiedCount, cyrillic=$cyrillicCount)';
}

/// Common characters whose Simplified form differs from Traditional and is
/// NOT also in normal use in Traditional text (so presence means Simplified).
const String _simplifiedOnly =
    '这们说时门东车长爱国个来对没还会见点开问间头发给关电书学实现经过动机'
    '马鸟鱼风飞饭饮从众优传伤价儿兴写决况准处务势协单卖华压县变号听吗员'
    '团围图场执报声够宝导层岁带师广应张强当录总惊户扑护拥拟择换损据'
    '摄数无晓暂术杂极标样桥检楼欢汉洁济测湾热环画异疗盖监'
    '码确离种称筑类红级约纪组结绝统继维罗网联肤胜脑艺节药规视览触记讨许论'
    '设访证评识诉词译试话该详语误请读调谈谁谢负贵费资赛赶轻转软较轮边运连进'
    '远选钱铁银错锁镇闭队阳阶际难页顾题颜验鸡麦齐龄龙乐乔习买乱争亏亚'
    '产亲亿仅仓仪优伙伟伦体佣侠侣侥侦侧俩债倾偿储兑党兰养兽'
    '册军农冯冲冻净凉减凑凤凭击凿刘则刚创删别刹剂剑剧劝办励劲劳'
    '勋匀协卢卫却厂厅历厉厌厕厨参双叙叠叹吓吕启吴呐响哑唤啸喷嘱园圆圣坏块坚坛'
    '坝坞坟坠垄垒垦垫埋壮壳壶备夹夺奋奖妈妇妆姗娄娱婴孙宁宝实审宪宫宽宾寻寿将'
    '尔尘尝尽屉届属屡岛岭岳币帅帐帘帮庄庆库庙废弃弥弯弹归彦彻忆忧怀态怜恋恳恶'
    '恼悬惨惯愤愿戏战扩扫扬扰抚抛抢担拢拣拦拨挂挡挣挥捣掳掷揽摆摇摊撑撵擞敌断'
    '旧晋晒晕杀权条杨构枪枫柜栅栈栏树桩梦棂椭榄榇欧歼残殴毁毕毙气汇汤沟沥沦沧'
    '泪泻泼泽洒浅浆浇浊浏浑浓涛涝涣润涨淀渊渐渔渗温湿溃滚滞满滤滥滨滩灭灯灵灾'
    '炉炼烁烂烛烟烦烧烫焕爷牵牺犹独狭狮猎猪猫献玛琐瑶畅疮疯痈瘫皱盏盐盘矿砖础'
    '硕碍礼祸禀秃积税稳窍竖笔笋筛签简粮紧纠纤纫纯纲纳纵纷纸纹纺纽线练细织终绍'
    '绕绘络绞绩绪续绳绵综绿缓编缘缩缴罚罢羡翘翻耸职聪肃肠肾胀胁脉脏脚脱腊腾艰'
    '芜苍苏苹茧荐荡荣莲获萝营萧蓝虑虫虽蚀蚁补装袄观觉觊誉计订认讥让训议讯讲讳'
    '讼诀诈诊诗诚诞诡询诸诺课谅谊谋谍谐谓谜谣谦谨谬谱贝贞贡财责贤败账货质贩贪'
    '贫购贯贰贷贸贺贻贼赁赊赋赌赎赏赐赔赖赚赞赠赢赵趋跃践踪轨轩轰载辅辆辈辉输'
    '辑辖辩辞辽达迁迈违迟适逊递逻遗邓邮邻郑酝酱释鉴銮钉针钓钙钝钞钟钢钥钦钩钮'
    '钳钻铃铅铜铝铭铺链销锅锈锋锐锡锤锦键锯镜镰闪闯闲闷闸闹闻阀阁阅阴阵陆陈险'
    '随隐雏雾静韩顶项顺须顽顿颁预领颇频颗额飘饥饰饱馆馈驱驳驴驶驻驼骂骄骆骑骗'
    '骚鲁鲜鸣鸭鸿黄';

final Set<int> _simplifiedSet = _simplifiedOnly.runes.toSet();

ZhCheck checkChinese(String text) {
  var cjk = 0, simp = 0, cyr = 0;
  for (final r in text.runes) {
    if ((r >= 0x4E00 && r <= 0x9FFF) || (r >= 0x3400 && r <= 0x4DBF)) {
      cjk++;
      if (_simplifiedSet.contains(r)) simp++;
    } else if (r >= 0x0400 && r <= 0x04FF) {
      cyr++;
    }
  }
  return ZhCheck(cjkCount: cjk, simplifiedCount: simp, cyrillicCount: cyr);
}

bool isTraditionalChinese(String text) => checkChinese(text).isTraditional;
