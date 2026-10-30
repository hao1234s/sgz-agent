import type {Schema, ToolDefinitions} from '../core/types.js';

const string:Schema={type:'string'};

const descriptions: ToolDefinitions['descriptions'] = {
skills_list:'列出三国志项目可供模型使用的 skills 摘要及原生目录扫描状态。技能按需加载；打地/打贼寇技能尚未编写时返回空目录。',
skill_load:'按准确名称加载三国志项目 SKILL.md 正文与资源基目录。执行匹配任务前先加载；技能文本不增加工具权限，不执行脚本或派兵。禁用模型调用的技能不可加载。'
};

const parameters: ToolDefinitions['parameters'] = {
skills_list:{properties:{},required:[]},
skill_load:{properties:{name:string},required:['name']}
};

export const definitions: ToolDefinitions = {descriptions,parameters};
