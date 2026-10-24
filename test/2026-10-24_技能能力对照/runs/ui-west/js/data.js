/*
 * Papertrail demo records.
 * Every record below is fictional demonstration material. The fields and
 * values are preserved from the supplied fixture; nothing here is a real
 * publication, and there is no remote catalogue behind it.
 */
(function (root) {
  'use strict';

  var PAPERS = [
    {id:'p01',title:'面向生成艺术的可控噪声场设计',authors:'林知远 · 陈悦',year:2026,venue:'计算视觉研究（演示）',topic:'视觉与图形',status:'reading',pages:14,abstract:'讨论多尺度噪声、轨迹积分与参数空间对生成图形结构的影响，并用种子与参数日志支持结果复现。',note:'重点看参数敏感性一节；和潮汐项目的轨迹算法有关。'},
    {id:'p02',title:'研究工具中的渐进披露与认知负担',authors:'周宁 · 王禾',year:2025,venue:'交互系统通讯（演示）',topic:'人机交互',status:'unread',pages:11,abstract:'比较信息密度、操作可见性与渐进披露在研究工作台中的作用，分析新用户与熟练用户的不同需求。',note:''},
    {id:'p03',title:'科研可视化中不确定性的多通道表达',authors:'许清 · 赵一帆',year:2026,venue:'可视分析前沿（演示）',topic:'视觉与图形',status:'read',pages:18,abstract:'通过颜色、形状与直接标注共同表达不确定性，评估不同编码对估计值解读和比较任务的影响。',note:'误差区间应说明统计定义，不能只写置信度。'},
    {id:'p04',title:'可复现文献筛选流程的版本记录',authors:'陈悦 · 刘川',year:2024,venue:'开放研究方法（演示）',topic:'科研方法',status:'unread',pages:9,abstract:'提出记录检索式、筛选标准、重复记录与排除理由的轻量工作流程，区分记录、报告与研究三个层级。',note:''},
    {id:'p05',title:'小屏幕知识管理界面的任务连续性',authors:'王禾 · 孙遥',year:2025,venue:'移动交互实践（演示）',topic:'人机交互',status:'reading',pages:12,abstract:'研究桌面与手机之间迁移阅读任务时的上下文保留，强调筛选状态、阅读位置与详情导航的一致性。',note:'可参考其中的列表与详情返回关系。'},
    {id:'p06',title:'粒子轨迹中的时间分层与视觉节奏',authors:'林知远 · 孙遥',year:2023,venue:'数字艺术实验（演示）',topic:'视觉与图形',status:'read',pages:16,abstract:'讨论粒子系统轨迹、局部密度和运动速度之间的关系，通过多层沉积构建具有时间感的计算图像。',note:'运动速度与轨迹长度的感受需要分开调节。'},
    {id:'p07',title:'从阅读笔记到研究问题的证据链',authors:'刘川 · 周宁',year:2026,venue:'开放研究方法（演示）',topic:'科研方法',status:'unread',pages:10,abstract:'分析笔记如何连接原文、论断与后续研究问题，并提出对事实、解释与假设分别标记的实践方式。',note:''},
    {id:'p08',title:'面向键盘用户的复杂筛选器反馈',authors:'赵一帆 · 许清',year:2025,venue:'交互系统通讯（演示）',topic:'人机交互',status:'read',pages:13,abstract:'比较复杂列表中焦点管理、筛选反馈和快捷操作的可发现性，强调视觉状态与辅助技术状态的对应。',note:'筛选后结果数反馈和焦点去向是两个问题。'},
    {id:'p09',title:'图形算法实验中的参数快照与比较',authors:'陈悦 · 林知远',year:2024,venue:'计算视觉研究（演示）',topic:'科研方法',status:'unread',pages:8,abstract:'提出对图形算法参数、环境和随机种子进行联合记录，帮助区分算法变化与随机样本造成的视觉差异。',note:''}
  ];

  var TOPICS = ['视觉与图形', '人机交互', '科研方法'];

  var data = {
    papers: PAPERS,
    topics: TOPICS,
    note: '所有记录均为虚构演示资料，不是真实文献；本页面不连接检索服务、账户或后端。'
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = data;
  }
  root.PAPERTRAIL_DATA = data;
})(typeof globalThis !== 'undefined' ? globalThis : this);
