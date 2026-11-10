import type {Schema, ToolDefinitions} from '../core/types.js';

const string:Schema={type:'string'};
const integer:Schema={type:'integer'};
const combatHeroes:Schema={type:'array',minItems:1,maxItems:3,items:{type:'object',additionalProperties:false,
  properties:{id:{type:'string',minLength:1},is_main:{type:'boolean'},troops:{type:'integer',minimum:0},
    capacity:{type:'integer',minimum:1},attribute_advantage:{type:'number',description:'可选、已核验的同目标属性差项 A；缺失时不估算完整输出比。'}},
  required:['id','is_main','troops','capacity']}};
const combatContext:Schema={type:'object',additionalProperties:false,properties:{
  ruleset:{type:'string',minLength:1,description:'客户端版本、赛季/剧本及士气/战斗规则的准确标识'},
  team_build:{type:'string',minLength:1,description:'完整编制配置指纹：等级/属性/红度/战法及等级/兵书/装备/适性/科技/兵种；不能只填队名'},
  target_profile:{type:'string',minLength:1,description:'目标模式、类型、等级、每队守军配置指纹；不能只填巨戟士等名称'},
  defender_waves:{type:'integer',minimum:1,maximum:100}},required:['ruleset','team_build','target_profile','defender_waves']};
const combatReports:Schema={type:'array',maxItems:100,items:{type:'object',additionalProperties:false,
  properties:{id:{type:'string',minLength:1},source:{type:'string',minLength:1},context:combatContext,heroes:combatHeroes,
    arrival_morale:{type:'number',minimum:0,maximum:1000},encounter_complete:{type:'boolean'},
    result:{type:'string',enum:['victory','defeat','draw','interrupted']},completed_waves:{type:'integer',minimum:0,maximum:100}},
  required:['id','source','context','heroes','arrival_morale','encounter_complete','result','completed_waves']}};

const descriptions: ToolDefinitions['descriptions'] = {
assess_combat:'通用战前评估，适用任意编制与目标配置：只计算传入的逐将兵力、预计到达士气和完整战报，不访问游戏。先加载 sgz-combat-assessment。士气系数、兵力诊断指数与胜负依据分开输出；不提供虚构胜率。已出征/交战/战平/未知阶段只返回等待，不检查兵力。不得为调用此工具在战斗中采集数据。'
};

const parameters: ToolDefinitions['parameters'] = {
assess_combat:{properties:{phase:{type:'string',enum:['idle','resolved','marching','fighting','draw','unknown','returning','recruiting','resting']},
    heroes:combatHeroes,arrival_morale:{type:'number',minimum:0,maximum:1000},
    observation_age_seconds:{type:'number',minimum:0},morale_rule_verified:{type:'boolean',default:false},
    min_total_troops:{type:'integer',minimum:0,default:0,description:'当前任务的兵力政策门槛，不是游戏胜利公式；巨戟士任务当前填20000'},
    context:combatContext,reports:combatReports},required:['phase']}
};

export const definitions: ToolDefinitions = {descriptions,parameters};
