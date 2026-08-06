import { Widget } from '@lumino/widgets';

export class FirstRunView extends Widget {
  constructor(options: { onCreateProfile: () => void }) {
    const node = document.createElement('section');
    super({ node });
    this.id = 'myextension-first-run';
    this.title.label = '编程行为观察';
    this.title.caption = '编程行为观察首次使用说明';
    node.className = 'jp-BehaviorAudit-firstRun';

    const title = document.createElement('h1');
    title.textContent = '开始一次有依据的教学观察';

    const answerHeading = document.createElement('h2');
    answerHeading.textContent = '这个工具能回答什么';
    const answer = document.createElement('p');
    answer.textContent =
      '教师先输入题目、确认知识点和测试方案，系统再按已发布方案整理编程过程中的行为证据与教学建议。';

    const collectHeading = document.createElement('h2');
    collectHeading.textContent = '会采集什么';
    const collect = document.createElement('p');
    collect.textContent =
      '仅在明确开始监控后采集编辑、运行、报错、页面可见性等客观过程事件。';

    const modelHeading = document.createElement('h2');
    modelHeading.textContent = '数据是否发送给外部模型';
    const model = document.createElement('p');
    model.textContent =
      '只有配置并使用外部模型时才会发送分析所需内容；请先确认本地配置和数据政策。';

    const configuration = document.createElement('p');
    configuration.textContent =
      '开始监控前，需要先为题目创建并发布一个试点考核方案。';

    const action = document.createElement('button');
    action.type = 'button';
    action.className =
      'jp-BehaviorAudit-button jp-BehaviorAudit-button-primary';
    action.textContent = '创建题目考核方案';
    action.addEventListener('click', options.onCreateProfile);

    node.append(
      title,
      answerHeading,
      answer,
      collectHeading,
      collect,
      modelHeading,
      model,
      configuration,
      action
    );
  }
}
