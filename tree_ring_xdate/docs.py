"""Built-in API documentation: OpenAPI 3 description and a single-page
HTML manual served at ``/`` so the service documents itself offline."""

import json

# Kept as a JSON literal so the parser itself guarantees balanced nesting.
OPENAPI = json.loads(r"""
{
  "openapi": "3.0.3",
  "info": {
    "title": "树轮交叉定年 API (Tree-ring cross-dating)",
    "version": "1.0.0",
    "description": "完全离线（仅 Python 标准库）的树轮宽度序列交叉定年服务。支持 JSON/CSV 上传、逐行校验定位、滑动相关候选、多假设锁定与 JSON 报告下载。"
  },
  "servers": [
    {
      "url": "/"
    }
  ],
  "tags": [
    {
      "name": "series",
      "description": "序列上传与校验"
    },
    {
      "name": "crossdating",
      "description": "滑动交叉定年"
    },
    {
      "name": "hypotheses",
      "description": "定年假设与锁定"
    },
    {
      "name": "corrections",
      "description": "缺失环/伪环校正草案"
    },
    {
      "name": "stability",
      "description": "已定年结果的局部稳定性检查"
    },
    {
      "name": "signal",
      "description": "年表信号强度评估（样本深度 / Rbar / EPS、可靠区间与状态流转）"
    },
    {
      "name": "chronology",
      "description": "主年表与统计"
    }
  ],
  "paths": {
    "/api/series": {
      "get": {
        "tags": [
          "series"
        ],
        "summary": "列出全部样本",
        "responses": {
          "200": {
            "description": "样本列表"
          }
        }
      },
      "post": {
        "tags": [
          "series"
        ],
        "summary": "上传 JSON 或 CSV 序列（多样本批量，错误定位到样本与原始行）",
        "parameters": [
          {
            "name": "strict",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "default": "0"
            },
            "description": "strict=1 时任一样本出错则整批不入库"
          },
          {
            "name": "filename",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            },
            "description": "帮助判定格式（*.csv / *.json）"
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/Payload"
              }
            },
            "text/csv": {
              "schema": {
                "type": "string"
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "saved 与 errors 汇总"
          },
          "422": {
            "description": "strict 模式下整批拒绝"
          }
        }
      }
    },
    "/api/series/{sample_id}": {
      "get": {
        "tags": [
          "series"
        ],
        "summary": "获取样本（含逐年轮宽与原始行号、原文 raw_payload）",
        "parameters": [
          {
            "name": "sample_id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "样本详情"
          },
          "404": {
            "description": "不存在"
          }
        }
      },
      "delete": {
        "tags": [
          "series"
        ],
        "summary": "删除样本",
        "parameters": [
          {
            "name": "sample_id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "已删除"
          }
        }
      }
    },
    "/api/crossdate": {
      "get": {
        "tags": [
          "crossdating"
        ],
        "summary": "滑动交叉定年（参数走 query）",
        "parameters": [
          {
            "name": "sample_id",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "reference",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "default": "master"
            },
            "description": "master 或某个已定年样本编号"
          },
          {
            "name": "offset_min",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "offset_max",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "min_overlap",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "default": 20
            }
          },
          {
            "name": "tolerance",
            "in": "query",
            "required": false,
            "schema": {
              "type": "number",
              "default": 0.05
            },
            "description": "与最高相关差值在容差内的候选全部保留（不自动定年）"
          },
          {
            "name": "top_k",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "default": 10
            }
          },
          {
            "name": "narrow_z",
            "in": "query",
            "required": false,
            "schema": {
              "type": "number",
              "default": -1.0
            }
          },
          {
            "name": "narrow_q",
            "in": "query",
            "required": false,
            "schema": {
              "type": "number",
              "default": 0.1
            }
          },
          {
            "name": "hypothesis",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            },
            "description": "参照主年表时纳入的锁定假设"
          },
          {
            "name": "standardization_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用标准化方案 id（别名 standardization）；指定后主年表/参照按其冻结轮宽指数构建，目标样本若在方案内则用其指数曲线，运行结果记录该版本"
          },
          {
            "name": "standardization_version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用版本号（缺省取方案当前 adopted_version）；不传 standardization_id 时保持原原始宽度口径"
          },
          {
            "name": "signal_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用信号强度评估 id（别名 signal_assessment）；指定后参照取其冻结成员指数并限制在可靠区间内，旧作业不受影响"
          }
        ],
        "responses": {
          "200": {
            "description": "候选偏移列表（offset/重叠区间/Pearson/符号一致率/极窄环命中年份）"
          }
        }
      },
      "post": {
        "tags": [
          "crossdating"
        ],
        "summary": "滑动交叉定年（参数走 JSON body）",
        "responses": {
          "200": {
            "description": "候选偏移列表"
          }
        },
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "properties": {
                  "standardization_id": {
                    "type": "integer",
                    "description": "已采用标准化方案 id（别名 standardization）；指定后主年表/参照按其冻结轮宽指数构建，目标样本若在方案内则用其指数曲线，运行结果记录该版本（body 字段，别名 standardization）"
                  },
                  "standardization_version": {
                    "type": "integer",
                    "description": "已采用版本号（缺省取方案当前 adopted_version）；不传 standardization_id 时保持原原始宽度口径"
                  },
                  "signal_id": {
                    "type": "integer",
                    "description": "已采用信号强度评估 id（别名 signal_assessment）；指定后参照取其冻结成员指数并限制在可靠区间内，运行结果记录该评估版本（body 字段，别名 signal_assessment）"
                  }
                }
              }
            }
          }
        }
      }
    },
    "/api/runs": {
      "get": {
        "tags": [
          "crossdating"
        ],
        "summary": "历史滑动计算及候选结果",
        "responses": {
          "200": {
            "description": "runs"
          }
        }
      }
    },
    "/api/corrections": {
      "post": {
        "tags": [
          "corrections"
        ],
        "summary": "从滑动候选偏移创建校正草案（缺失环/伪环事件），返回分段试算",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/CorrectionDraft"
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "草案已创建（含分段映射、各段与全序列统计、相对原候选的变化）"
          },
          "404": {
            "description": "样本或运行候选不存在"
          },
          "422": {
            "description": "事件校验失败（重复/次序/越界/与已标缺失环或显式年份冲突），errors 逐条定位测量序号"
          }
        }
      },
      "get": {
        "tags": [
          "corrections"
        ],
        "summary": "列出校正草案（?sample_id= 过滤）",
        "parameters": [
          {
            "name": "sample_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "草案列表"
          }
        }
      }
    },
    "/api/corrections/{id}": {
      "get": {
        "tags": [
          "corrections"
        ],
        "summary": "草案最新版本及分段试算（?version=N 查看指定版本）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "草案版本 + 分段映射 + 评估"
          },
          "404": {
            "description": "草案或版本不存在"
          }
        }
      }
    },
    "/api/corrections/{id}/preview": {
      "get": {
        "tags": [
          "corrections"
        ],
        "summary": "按事件重建分段年份映射，重算各段及全序列重叠区间、Pearson、符号一致率、共同极窄环，并列出相对原候选的变化",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "分段试算结果（evaluation.segments / evaluation.whole / changes_vs_candidate）"
          }
        }
      }
    },
    "/api/corrections/{id}/versions": {
      "get": {
        "tags": [
          "corrections"
        ],
        "summary": "草案全部版本",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "版本列表"
          }
        }
      },
      "post": {
        "tags": [
          "corrections"
        ],
        "summary": "替换事件列表生成新版本（采用中的草案也可准备替代版本，live 映射仍钉在已采用版本）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "events": {
                    "type": "array",
                    "items": {
                      "$ref": "#/components/schemas/CorrectionEvent"
                    }
                  },
                  "note": {
                    "type": "string"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "新版本已创建"
          },
          "422": {
            "description": "事件校验失败"
          }
        }
      }
    },
    "/api/corrections/{id}/adopt": {
      "post": {
        "tags": [
          "corrections"
        ],
        "summary": "采用草案：分段映射写入指定假设并进入主年表；任一有效分段不足 min_overlap 时 422 拒绝并定位测量序号",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "hypothesis": {
                    "type": "string",
                    "default": "default"
                  },
                  "version": {
                    "type": "integer",
                    "description": "缺省采用最新版本"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "已采用（返回分段评估）"
          },
          "422": {
            "description": "分段不足最小重叠（E_SEGMENT_TOO_SHORT）或草案已采用"
          }
        }
      }
    },
    "/api/corrections/{id}/revoke": {
      "post": {
        "tags": [
          "corrections"
        ],
        "summary": "撤销采用，样本恢复原 placement",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "已撤销"
          },
          "422": {
            "description": "草案未处于采用状态"
          }
        }
      }
    },
    "/api/corrections/{id}/compare": {
      "get": {
        "tags": [
          "corrections"
        ],
        "summary": "比较两个草案版本的事件与统计差异（?a=1&b=2）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "a",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "b",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "事件增删 + 全序列统计差值 + 分段对比"
          }
        }
      }
    },
    "/api/corrections/{id}/download": {
      "get": {
        "tags": [
          "corrections"
        ],
        "summary": "草案完整 JSON 导出（来源运行、参照快照、全部版本与评估；?download=0 取消附件头）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "download",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "default": "1"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "JSON 报告（Content-Disposition 附件）"
          }
        }
      }
    },
    "/api/stability": {
      "post": {
        "tags": [
          "stability"
        ],
        "summary": "发起局部稳定性检查：按采用后的年份映射切重叠窗口，各窗口在当前位置两侧滑动比对",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/StabilityCheck"
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "检查完成（逐窗口 best_shift、并列偏移、统计量与疑似错位标记）"
          },
          "404": {
            "description": "假设不存在，或样本在该假设中无锁定/校正映射"
          },
          "422": {
            "description": "参数越界（E_PARAM）、假设内无可检查样本（E_NO_TARGETS）、参照即目标自身（E_SELF_REFERENCE）或排除目标后无独立参照成员（E_NO_INDEPENDENT_REFERENCE）"
          }
        }
      },
      "get": {
        "tags": [
          "stability"
        ],
        "summary": "列出检查作业（?hypothesis=&sample_id= 过滤）",
        "parameters": [
          {
            "name": "hypothesis",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "sample_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "检查列表（不含窗口明细）"
          }
        }
      }
    },
    "/api/stability/{id}": {
      "get": {
        "tags": [
          "stability"
        ],
        "summary": "检查详情；窗口可按年份、偏移与状态筛选；参照/映射失效只说明依据",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "year_from",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "只保留与该年及之后相交的窗口"
          },
          {
            "name": "year_to",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "只保留与该年及之前相交的窗口"
          },
          {
            "name": "shift",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "按窗口最佳偏移 best_shift 精确筛选（0=当前位置）"
          },
          {
            "name": "status",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "ok",
                "insufficient_coverage"
              ]
            }
          },
          {
            "name": "sample_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "检查详情（windows/flags/reference_status/mapping_status）"
          },
          "404": {
            "description": "检查不存在"
          }
        }
      }
    },
    "/api/stability/compare": {
      "get": {
        "tags": [
          "stability"
        ],
        "summary": "比较两次检查：参数差异、共有窗口 best_shift 变化、标记新增与消失（?a=&b=）",
        "parameters": [
          {
            "name": "a",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "b",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "两次检查的差异"
          },
          "404": {
            "description": "任一检查不存在"
          }
        }
      }
    },
    "/api/stability/{id}/download": {
      "get": {
        "tags": [
          "stability"
        ],
        "summary": "检查完整 JSON 导出（参数、样本映射、参照快照、全部窗口与标记；?download=0 取消附件头）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "download",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "default": "1"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "JSON 报告（Content-Disposition 附件）"
          }
        }
      }
    },
    "/api/signal": {
      "post": {
        "tags": [
          "signal"
        ],
        "summary": "创建年表信号强度评估（draft）：冻结定年假设、可选标准化版本、样本集合与窗口参数，逐窗计算深度/Rbar/EPS 与逐样本剔除差值",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/SignalAssessment"
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "评估完成（逐窗样本深度、样本对相关、Rbar、EPS、jackknife 差值与公式输入）"
          },
          "404": {
            "description": "假设或样本不存在"
          },
          "409": {
            "description": "同名评估已存在（E_SIGNAL_EXISTS）"
          },
          "422": {
            "description": "参数越界（E_PARAM）、假设内无样本（E_NO_MEMBERS）、无可冻结成员（E_NO_ASSESSABLE_MEMBERS）或标准化方案未覆盖某样本（E_STD_NOT_COVERED）"
          }
        }
      },
      "get": {
        "tags": [
          "signal"
        ],
        "summary": "列出评估作业（?hypothesis=&status=&sample_id= 过滤）",
        "parameters": [
          {
            "name": "hypothesis",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "status",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "draft",
                "completed",
                "adopted",
                "retired"
              ]
            }
          },
          {
            "name": "sample_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            },
            "description": "只返回冻结成员包含该样本的评估"
          }
        ],
        "responses": {
          "200": {
            "description": "评估列表（不含逐窗明细）"
          }
        }
      }
    },
    "/api/signal/{id}": {
      "get": {
        "tags": [
          "signal"
        ],
        "summary": "评估详情；窗口可按年份/状态/EPS 达标/成员筛选；来源变化只标记 stale 不重算",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "year_from",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "year_to",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "status",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "ok",
                "insufficient_coverage",
                "no_valid_pairs",
                "eps_incalculable"
              ]
            }
          },
          {
            "name": "eps_pass",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "0",
                "1"
              ]
            },
            "description": "只保留 EPS 达标/未达标窗口"
          },
          {
            "name": "sample_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "评估详情（windows/source_status/reliable_span）"
          },
          "404": {
            "description": "评估不存在"
          }
        }
      }
    },
    "/api/signal/{id}/complete": {
      "post": {
        "tags": [
          "signal"
        ],
        "summary": "完成评估（draft→completed），冻结证据不变",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "评估详情（status=completed）"
          },
          "404": {
            "description": "评估不存在"
          },
          "422": {
            "description": "评估已采用（E_SIGNAL_ALREADY_ADOPTED）或已停用（E_SIGNAL_RETIRED）"
          }
        }
      }
    },
    "/api/signal/{id}/adopt": {
      "post": {
        "tags": [
          "signal"
        ],
        "summary": "采用连续 EPS 达标窗口为可靠区间（completed→adopted，一次性不可变）；未达阈值的区间不得采用，已采用后再次调用被拒绝",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "windows": {
                    "type": "array",
                    "items": {
                      "type": "integer"
                    },
                    "description": "相邻且全部达标的窗口下标；缺省取最长连续达标段"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "已采用（reliable_span 给出窗口下标与年份范围）"
          },
          "404": {
            "description": "评估不存在"
          },
          "422": {
            "description": "仍是 draft（E_SIGNAL_DRAFT）、已采用且不可再改（E_SIGNAL_ALREADY_ADOPTED）、已停用（E_SIGNAL_RETIRED）、无达标窗口（E_NO_RELIABLE_RANGE）、含未达标窗口（E_WINDOW_BELOW_THRESHOLD）或窗口不相邻（E_WINDOWS_NOT_CONSECUTIVE）"
          }
        }
      }
    },
    "/api/signal/{id}/retire": {
      "post": {
        "tags": [
          "signal"
        ],
        "summary": "停用评估（completed/adopted→retired）；旧作业保持原范围",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "已停用"
          },
          "404": {
            "description": "评估不存在"
          },
          "422": {
            "description": "评估已是 retired（E_SIGNAL_RETIRED）"
          }
        }
      }
    },
    "/api/signal/compare": {
      "get": {
        "tags": [
          "signal"
        ],
        "summary": "比较两次评估：参数差异、共有窗口 Rbar/EPS 与达标变化、可靠区间差异（?a=&b=）",
        "parameters": [
          {
            "name": "a",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "b",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "两次评估的差异"
          },
          "404": {
            "description": "任一评估不存在"
          }
        }
      }
    },
    "/api/signal/{id}/download": {
      "get": {
        "tags": [
          "signal"
        ],
        "summary": "评估完整 JSON 导出（冻结来源映射、逐窗统计与采用范围；?download=0 取消附件头）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "download",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "default": "1"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "JSON 报告（Content-Disposition 附件）"
          },
          "404": {
            "description": "评估不存在"
          }
        }
      }
    },
    "/api/hypotheses": {
      "get": {
        "tags": [
          "hypotheses"
        ],
        "summary": "列出假设",
        "responses": {
          "200": {
            "description": "假设列表"
          }
        }
      },
      "post": {
        "tags": [
          "hypotheses"
        ],
        "summary": "新建定年假设",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "name": {
                    "type": "string"
                  },
                  "note": {
                    "type": "string"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "已创建"
          },
          "409": {
            "description": "同名已存在"
          }
        }
      }
    },
    "/api/hypotheses/{name}": {
      "get": {
        "tags": [
          "hypotheses"
        ],
        "summary": "假设详情（含全部锁定）",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "假设"
          }
        }
      },
      "delete": {
        "tags": [
          "hypotheses"
        ],
        "summary": "删除假设",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "已删除"
          }
        }
      }
    },
    "/api/hypotheses/{name}/locks": {
      "post": {
        "tags": [
          "hypotheses"
        ],
        "summary": "锁定样本偏移（offset=第 1 轮日历年；可用 start_year 别名）",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "sample_id": {
                    "type": "string"
                  },
                  "offset": {
                    "type": "integer"
                  },
                  "start_year": {
                    "type": "integer"
                  },
                  "run_id": {
                    "type": "integer"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "锁定结果"
          }
        }
      },
      "delete": {
        "tags": [
          "hypotheses"
        ],
        "summary": "撤销锁定",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "sample_id",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "已撤销"
          }
        }
      }
    },
    "/api/hypotheses/{name}/chronology": {
      "get": {
        "tags": [
          "chronology"
        ],
        "summary": "逐年样本数、均值、中位数、离散度与 LOW_COVERAGE/OUTLIER/LOCK_CONFLICT 标记",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "min_samples",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer",
              "default": 3
            }
          },
          {
            "name": "outlier_sd",
            "in": "query",
            "required": false,
            "schema": {
              "type": "number",
              "default": 2.0
            }
          },
          {
            "name": "weak_correlation",
            "in": "query",
            "required": false,
            "schema": {
              "type": "number",
              "default": 0.3
            }
          },
          {
            "name": "standardization_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用标准化方案 id（别名 standardization）；指定后主年表/参照按其冻结轮宽指数构建，目标样本若在方案内则用其指数曲线，运行结果记录该版本"
          },
          {
            "name": "standardization_version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用版本号（缺省取方案当前 adopted_version）；不传 standardization_id 时保持原原始宽度口径"
          },
          {
            "name": "signal_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用信号强度评估 id（别名 signal_assessment）；指定后年表取该评估冻结成员指数并只覆盖可靠区间，旧作业保持原范围"
          }
        ],
        "responses": {
          "200": {
            "description": "年表统计"
          }
        }
      }
    },
    "/api/hypotheses/{name}/report": {
      "get": {
        "tags": [
          "hypotheses"
        ],
        "summary": "完整 JSON 报告（?download=1 附 Content-Disposition）",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "download",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            },
            "description": "1/true 时作为附件下载"
          }
        ],
        "responses": {
          "200": {
            "description": "JSON 报告"
          }
        }
      }
    },
    "/api/hypotheses/{name}/vs-master": {
      "get": {
        "tags": [
          "hypotheses"
        ],
        "summary": "锁定位置与已定年主年表的逐年差异",
        "parameters": [
          {
            "name": "name",
            "in": "path",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "逐样本 shift"
          }
        }
      }
    },
    "/api/compare": {
      "get": {
        "tags": [
          "hypotheses"
        ],
        "summary": "比较两个定年假设的样本位置",
        "parameters": [
          {
            "name": "a",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "b",
            "in": "query",
            "required": true,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "差异"
          }
        }
      }
    },
    "/api/master": {
      "get": {
        "tags": [
          "chronology"
        ],
        "summary": "当前主年表逐年指数",
        "parameters": [
          {
            "name": "hypothesis",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "standardization_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用标准化方案 id（别名 standardization）；指定后主年表/参照按其冻结轮宽指数构建，目标样本若在方案内则用其指数曲线，运行结果记录该版本"
          },
          {
            "name": "standardization_version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用版本号（缺省取方案当前 adopted_version）；不传 standardization_id 时保持原原始宽度口径"
          },
          {
            "name": "signal_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用信号强度评估 id（别名 signal_assessment）；指定后主年表取该评估冻结成员指数并只覆盖可靠区间，旧作业保持原范围"
          }
        ],
        "responses": {
          "200": {
            "description": "master"
          }
        }
      }
    },
    "/api/chronology": {
      "get": {
        "tags": [
          "chronology"
        ],
        "summary": "默认假设年表（未指定假设时使用 default）",
        "parameters": [
          {
            "name": "hypothesis",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          },
          {
            "name": "standardization_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用标准化方案 id（别名 standardization）；指定后主年表/参照按其冻结轮宽指数构建，目标样本若在方案内则用其指数曲线，运行结果记录该版本"
          },
          {
            "name": "standardization_version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用版本号（缺省取方案当前 adopted_version）；不传 standardization_id 时保持原原始宽度口径"
          },
          {
            "name": "signal_id",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            },
            "description": "已采用信号强度评估 id（别名 signal_assessment）；指定后年表取该评估冻结成员指数并只覆盖可靠区间，旧作业保持原范围"
          }
        ],
        "responses": {
          "200": {
            "description": "年表统计"
          }
        }
      }
    },
    "/api/help": {
      "get": {
        "summary": "中文端点速查",
        "responses": {
          "200": {
            "description": "help"
          }
        }
      }
    },
    "/api/standardizations": {
      "get": {
        "tags": [
          "standardization"
        ],
        "summary": "列出序列标准化方案（?status=&hypothesis= 过滤）",
        "parameters": [
          {
            "name": "status",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "enum": [
                "draft",
                "validated",
                "adopted",
                "retired"
              ]
            }
          },
          {
            "name": "hypothesis",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "方案列表"
          }
        }
      },
      "post": {
        "tags": [
          "standardization"
        ],
        "summary": "创建标准化方案（draft，版本1）：逐样本选择水平均值/负指数曲线/固定窗口居中移动平均",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/StandardizationCreate"
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "方案已创建并返回最新版本预览（逐样本期望生长曲线与轮宽指数）"
          },
          "404": {
            "description": "假设或样本不存在"
          },
          "409": {
            "description": "同名方案已存在（E_STD_EXISTS）"
          },
          "422": {
            "description": "配置结构错误（方法名/移动窗口参数/重复样本）；样本级拟合问题只在预览中标记 valid=false，不阻止建草案"
          }
        }
      }
    },
    "/api/standardizations/{id}": {
      "get": {
        "tags": [
          "standardization"
        ],
        "summary": "方案详情与指定版本预览（?version=N，默认最新；已采用版本返回冻结曲线）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "方案元数据、config 与逐样本曲线/指数/诊断"
          },
          "404": {
            "description": "方案或版本不存在"
          }
        }
      }
    },
    "/api/standardizations/{id}/preview": {
      "get": {
        "tags": [
          "standardization"
        ],
        "summary": "按 ?version=N 预览期望生长曲线与轮宽指数（默认最新版本）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "version",
            "in": "query",
            "required": false,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "预览"
          },
          "404": {
            "description": "方案或版本不存在"
          }
        }
      },
      "post": {
        "tags": [
          "standardization"
        ],
        "summary": "临时试算候选 samples（不生成版本）；也可在 body 带 version 预览已有版本",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "samples": {
                    "type": "array",
                    "items": {
                      "$ref": "#/components/schemas/StdSampleChoice"
                    }
                  },
                  "version": {
                    "type": "integer"
                  }
                },
                "description": "samples 给出时为临时试算（ad_hoc=true），不写入任何版本"
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "试算结果"
          },
          "422": {
            "description": "配置或逐样本校验失败"
          }
        }
      }
    },
    "/api/standardizations/{id}/versions": {
      "get": {
        "tags": [
          "standardization"
        ],
        "summary": "列出方案全部版本（含是否已冻结来源映射/曲线）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "版本列表"
          },
          "404": {
            "description": "方案不存在"
          }
        }
      },
      "post": {
        "tags": [
          "standardization"
        ],
        "summary": "以新 samples 配置生成新版本（adopted/retired 不可改；编辑 validated 方案回到 draft）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": [
                  "samples"
                ],
                "properties": {
                  "samples": {
                    "type": "array",
                    "items": {
                      "$ref": "#/components/schemas/StdSampleChoice"
                    }
                  },
                  "note": {
                    "type": "string"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "新版本已创建"
          },
          "404": {
            "description": "方案或样本不存在"
          },
          "422": {
            "description": "配置错误或方案已采用/停用（E_STD_IMMUTABLE）"
          }
        }
      }
    },
    "/api/standardizations/{id}/validate": {
      "post": {
        "tags": [
          "standardization"
        ],
        "summary": "全样本校验：全部通过后 draft->validated，否则 422 并逐样本返回测量序号（不自动更换算法）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "version": {
                    "type": "integer"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "全部样本通过（方案转为 validated）"
          },
          "404": {
            "description": "方案或版本不存在"
          },
          "422": {
            "description": "存在未通过样本：E_TOO_FEW_VALID_POINTS / E_EXP_NOT_CONVERGED / E_EXPECTED_NONPOSITIVE / E_WINDOW_TOO_SHORT / E_NO_MAPPING，errors 逐项带 sample_id、method 与 seq/seqs"
          }
        }
      }
    },
    "/api/standardizations/{id}/adopt": {
      "post": {
        "tags": [
          "standardization"
        ],
        "summary": "采用方案：全样本校验通过后冻结来源映射（含校正版本）、参数与拟合曲线（仅 adopted 版本可被主年表/滑动匹配/稳定性检查指定）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "version": {
                    "type": "integer",
                    "description": "默认最新版本"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "方案已采用（status=adopted，版本被冻结）"
          },
          "404": {
            "description": "方案或版本不存在"
          },
          "422": {
            "description": "存在未通过样本、方案已采用（E_STD_ADOPTED）或已停用（E_STD_RETIRED）"
          }
        }
      }
    },
    "/api/standardizations/{id}/retire": {
      "post": {
        "tags": [
          "standardization"
        ],
        "summary": "停用方案（retired 不可再改或再采用；已引用该版本的旧作业仍保留原计算依据）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "方案已停用"
          },
          "404": {
            "description": "方案不存在"
          },
          "422": {
            "description": "方案已是 retired（E_STD_RETIRED）"
          }
        }
      }
    },
    "/api/standardizations/{id}/compare": {
      "get": {
        "tags": [
          "standardization"
        ],
        "summary": "比较两个方案版本：样本增删、方法/参数变化与拟合诊断（?a=&b=）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "a",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "b",
            "in": "query",
            "required": true,
            "schema": {
              "type": "integer"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "版本差异"
          },
          "404": {
            "description": "方案或任一端点版本不存在"
          }
        }
      }
    },
    "/api/standardizations/{id}/download": {
      "get": {
        "tags": [
          "standardization"
        ],
        "summary": "方案完整 JSON 导出（全部版本、冻结来源映射、期望曲线、指数与诊断；?download=0 取消附件头）",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": {
              "type": "integer"
            }
          },
          {
            "name": "download",
            "in": "query",
            "required": false,
            "schema": {
              "type": "string",
              "default": "1"
            }
          }
        ],
        "responses": {
          "200": {
            "description": "JSON 报告（Content-Disposition 附件）"
          },
          "404": {
            "description": "方案不存在"
          }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "CorrectionEvent": {
        "type": "object",
        "description": "校正事件。missing_ring：在测量序号 after_seq 之后插入 years 个缺失日历年（after_seq=0 表示首测量之前）；false_ring：把测量 seq 标为不参与定年的伪环（保留宽度，不分配日历年）。",
        "properties": {
          "type": {
            "type": "string",
            "enum": [
              "missing_ring",
              "false_ring"
            ]
          },
          "after_seq": {
            "type": "integer",
            "description": "missing_ring 专用：在此测量序号之后插入缺失年（0..n）"
          },
          "years": {
            "type": "integer",
            "default": 1,
            "description": "missing_ring 专用：插入的缺失日历年个数（1..100）"
          },
          "seq": {
            "type": "integer",
            "description": "false_ring 专用：伪环测量序号（1..n）"
          }
        },
        "required": [
          "type"
        ]
      },
      "CorrectionDraft": {
        "type": "object",
        "description": "校正草案创建请求。offset 为第 1 个测量对应的日历年（通常取一次滑动匹配的候选偏移，run_id 用于关联来源运行并做相对变化对比）。",
        "properties": {
          "sample_id": {
            "type": "string"
          },
          "offset": {
            "type": "integer"
          },
          "run_id": {
            "type": "integer"
          },
          "reference": {
            "type": "string",
            "default": "master"
          },
          "hypothesis": {
            "type": "string"
          },
          "min_overlap": {
            "type": "integer",
            "default": 20
          },
          "narrow_z": {
            "type": "number",
            "default": -1.0
          },
          "narrow_q": {
            "type": "number",
            "default": 0.1
          },
          "note": {
            "type": "string"
          },
          "events": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/CorrectionEvent"
            }
          }
        },
        "required": [
          "sample_id",
          "offset",
          "events"
        ]
      },
      "StabilityCheck": {
        "type": "object",
        "description": "局部稳定性检查请求。针对假设中已锁定（或已采用校正）的样本：按采用后的年份映射切出 window 年、步长 step 的重叠窗口（尾窗 end_year 收窄到映射实际最大年份），缺失年保留零宽参与统计、伪环不参与；每个窗口在当前位置 ±search_radius 内与参照滑动比对，仅共同年份达到 min_valid_years 的偏移才参与结论，全部偏移都不足时该窗口标记 insufficient_coverage 并给出最佳共同年数作为依据。参照为 master 时按 leave-one-out 排除被检查样本自身（防止自证）；指定参照不得为被检查目标（E_SELF_REFERENCE），目标被排除后无独立参照成员时 422（E_NO_INDEPENDENT_REFERENCE）。相邻窗口连续 run_threshold 个偏向同一非零位移时标出疑似错位区间与测量序号。检查只读：不改锁定或校正草案。",
        "properties": {
          "hypothesis": {
            "type": "string",
            "description": "要检查的定年假设（必填）"
          },
          "sample_id": {
            "type": "string",
            "description": "缺省检查假设中全部已锁定/已校正样本"
          },
          "reference": {
            "type": "string",
            "default": "master",
            "description": "master 或某个已定年样本编号"
          },
          "window": {
            "type": "integer",
            "default": 30,
            "description": "窗口长度（年，>=5）"
          },
          "step": {
            "type": "integer",
            "default": 10,
            "description": "相邻窗口起点步长（年，>=1）"
          },
          "min_valid_years": {
            "type": "integer",
            "default": 15,
            "description": "窗口最少有效年数（3..window），不足则该窗口标记 insufficient_coverage"
          },
          "run_threshold": {
            "type": "integer",
            "default": 3,
            "description": "触发疑似错位标记所需的连续窗口数（>=2）"
          },
          "search_radius": {
            "type": "integer",
            "default": 3,
            "description": "当前位置两侧的搜索范围（±年，1..25）"
          },
          "tolerance": {
            "type": "number",
            "default": 0.05,
            "description": "与最高相关差值在容差内的偏移并列保留"
          },
          "narrow_z": {
            "type": "number",
            "default": -1.0
          },
          "narrow_q": {
            "type": "number",
            "default": 0.1
          },
          "note": {
            "type": "string"
          },
          "standardization_id": {
            "type": "integer",
            "description": "已采用标准化方案 id（别名 standardization）；指定后主年表/参照按其冻结轮宽指数构建，目标样本若在方案内则用其指数曲线，运行结果记录该版本"
          },
          "standardization_version": {
            "type": "integer",
            "description": "已采用版本号（缺省取方案当前 adopted_version）；不传 standardization_id 时保持原原始宽度口径"
          }
        },
        "required": [
          "hypothesis"
        ]
      },
      "SignalAssessment": {
        "type": "object",
        "description": "年表信号强度评估请求。创建即冻结定年假设、可选已采用标准化版本、样本集合与窗口参数；逐窗仅在<b>该窗口年份范围内</b>的成员共同年份上计算样本深度、样本对 Pearson、Rbar（有效配对相关的均值；配对在窗口内需至少 min_pair_years 个共同年份且双方非零方差，绝不跨窗口用整段共同年份）与 EPS=N·R̄/(N·R̄+1−R̄)（N 为窗口平均样本深度），列出参与样本、有效配对数与全部公式输入；再逐个剔除样本重算 Rbar/EPS 差值（jackknife，仅提示，绝不自动排除任何序列）。覆盖不足、无有效配对或 EPS 无法计算的窗口只返回依据（insufficient_coverage/no_valid_pairs/eps_incalculable）。作业按 draft→completed→adopted→retired 流转：只有 completed 才能 adopt，采用连续 EPS 达标窗口作为可靠区间；采用是一次性不可变操作，再次 adopt 返回 E_SIGNAL_ALREADY_ADOPTED；来源假设/校正/标准化版本变化只把旧评估标记为 stale，不重算。",
        "properties": {
          "name": {
            "type": "string",
            "description": "评估唯一名（必填）"
          },
          "hypothesis": {
            "type": "string",
            "description": "提供年份映射的定年假设（必填）"
          },
          "samples": {
            "type": "array",
            "items": {
              "type": "string"
            },
            "description": "样本编号集合；缺省取假设内全部已锁定/已校正样本"
          },
          "window": {
            "type": "integer",
            "default": 50,
            "description": "窗口长度（年，>=5）"
          },
          "step": {
            "type": "integer",
            "default": 25,
            "description": "相邻窗口起点步长（年，>=1）"
          },
          "min_samples": {
            "type": "integer",
            "default": 3,
            "description": "窗口平均样本深度至少达到的样本数（>=2），不足标记 insufficient_coverage"
          },
          "eps_threshold": {
            "type": "number",
            "default": 0.85,
            "description": "EPS 达标阈值（严格介于 0..1，常用 0.85）"
          },
          "min_pair_years": {
            "type": "integer",
            "default": 5,
            "description": "样本对有效所需的最少共同年份（>=3）"
          },
          "note": {
            "type": "string"
          },
          "standardization_id": {
            "type": "integer",
            "description": "已采用标准化方案 id（别名 standardization）；指定后成员指数取自该版本冻结轮宽指数，样本必须全部被该方案覆盖"
          },
          "standardization_version": {
            "type": "integer",
            "description": "已采用版本号（缺省取方案当前 adopted_version）"
          }
        },
        "required": [
          "name",
          "hypothesis"
        ]
      },
      "StdSampleChoice": {
        "type": "object",
        "description": "一个样本的去趋势选择。method=mean 水平均值（无需参数）；negative_exponential 负指数曲线 a0*exp(b*(year-origin_year))+d（b<=0，阻尼 Gauss-Newton 拟合，不收敛返回 E_EXP_NOT_CONVERGED 且不更换算法）；moving_average 固定窗口居中移动平均，parameters.window 必须为 >=3 的奇数，窗口内有效点不足 3 个时返回 E_WINDOW_TOO_SHORT 并定位 seq。缺失环保留零宽（指数记 0），伪环不分配日历年、不参与拟合。",
        "properties": {
          "sample_id": {
            "type": "string"
          },
          "method": {
            "type": "string",
            "enum": [
              "mean",
              "negative_exponential",
              "moving_average"
            ]
          },
          "parameters": {
            "type": "object",
            "properties": {
              "window": {
                "type": "integer",
                "minimum": 3,
                "description": "moving_average 专用：居中窗口年数，必须为奇数"
              }
            }
          },
          "note": {
            "type": "string"
          }
        },
        "required": [
          "sample_id",
          "method"
        ]
      },
      "StandardizationCreate": {
        "type": "object",
        "description": "标准化方案创建请求。方案从 hypothesis 当前采用的年份映射（锁定 offset 或已采用校正草案的分段映射）读取年份与校正版本。草案可直接创建；全部样本校验通过（POST .../validate）才能成为 validated，采用（.../adopt）时冻结来源映射、参数、拟合曲线与诊断，之后主年表/滑动匹配/稳定性检查可用 standardization_id(+standardization_version) 指定该版本。",
        "properties": {
          "name": {
            "type": "string",
            "description": "方案唯一名（必填）"
          },
          "hypothesis": {
            "type": "string",
            "description": "提供年份映射的定年假设（必填）"
          },
          "note": {
            "type": "string"
          },
          "samples": {
            "type": "array",
            "items": {
              "$ref": "#/components/schemas/StdSampleChoice"
            }
          }
        },
        "required": [
          "name",
          "hypothesis",
          "samples"
        ]
      },
      "Payload": {
        "type": "object",
        "description": "单样本对象或 {\"samples\": [...]} 批量。CSV 表头列名支持中英别名：sample_id/sample/样本编号, unit/单位, start_year/起始年份, year/年份, width/宽度, missing/缺失环。",
        "properties": {
          "sample_id": {
            "type": "string"
          },
          "unit": {
            "type": "string"
          },
          "start_year": {
            "type": [
              "integer",
              "null"
            ]
          },
          "rings": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "year": {
                  "type": [
                    "integer",
                    "null"
                  ]
                },
                "width": {
                  "type": "number"
                },
                "missing": {
                  "type": "boolean"
                }
              }
            }
          }
        },
        "example": {
          "sample_id": "A01",
          "unit": "mm",
          "start_year": 1950,
          "rings": [
            {
              "width": 1.21
            },
            {
              "width": 0.98
            },
            {
              "width": 0,
              "missing": true
            }
          ]
        }
      },
      "Error": {
        "type": "object",
        "properties": {
          "code": {
            "type": "string",
            "description": "E_UNIT_CONFLICT / E_NONPOSITIVE_WIDTH / E_DUPLICATE_YEAR / E_CONFLICTING_MARK / E_BAD_YEAR / ..."
          },
          "message": {
            "type": "string"
          },
          "sample_id": {
            "type": [
              "string",
              "null"
            ]
          },
          "line": {
            "type": [
              "integer",
              "null"
            ],
            "description": "CSV 物理行号（表头为第 1 行）"
          },
          "row": {
            "type": [
              "integer",
              "null"
            ],
            "description": "样本内数据行 / JSON 项序号"
          },
          "field": {
            "type": [
              "string",
              "null"
            ]
          }
        }
      }
    }
  }
}""")

HTML_DOCS = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>树轮交叉定年 API</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:960px;
margin:2rem auto;padding:0 1rem;line-height:1.55;color:#222}
h1{border-bottom:3px solid #5a7a4a;padding-bottom:.3rem}
h2{color:#3a5a2a;margin-top:2rem}
code,pre{font-family:ui-monospace,Menlo,Consolas,monospace}
pre{background:#f4f6f1;border:1px solid #d4dcc9;border-radius:6px;
padding:.8rem;overflow:auto;font-size:.88rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0}
th,td{border:1px solid #cdd4c3;padding:.35rem .5rem;text-align:left;
vertical-align:top;font-size:.92rem}
th{background:#eef2e7}
.tag{display:inline-block;background:#e2ecdc;border-radius:4px;
padding:0 .35rem;font-size:.8rem;color:#3a5a2a}
.note{background:#fff8e6;border-left:4px solid #d7a93c;padding:.5rem .8rem}
</style></head><body>
<h1>树轮交叉定年 API（离线版）</h1>
<p>仅依赖 Python 标准库（<code>http.server</code> + <code>sqlite3</code>）。
机器可读描述见 <a href="/api/openapi.json">/api/openapi.json</a>，
端点速查见 <a href="/api/help">/api/help</a>。</p>

<h2>1. 启动</h2>
<pre>python -m tree_ring_xdate --db tree_ring.db --host 127.0.0.1 --port 8000</pre>

<h2>2. 上传序列 <span class="tag">POST /api/series</span></h2>
<p>支持 <code>Content-Type: application/json</code> 或
<code>text/csv</code>。多样本批量上传时，每个样本独立校验，
错误定位到<b>样本编号 + 原始行</b>（CSV 为物理行号，表头是第 1 行；
JSON 为 rings 数组项序号）。已接受样本的原文保存在数据库
<code>series.raw_payload</code>，<b>输入逐字保持原样</b>：JSON 保留请求正文中该样本的
原始换行、缩进和自定义空格（不做重新序列化），CSV 保留该样本的原始行。</p>
<p>CSV 列名（中英别名均可）：<code>sample_id, unit, start_year, year,
width, missing</code>。同一文件可含多个样本：留空
<code>sample_id/unit/start_year</code> 的行延续上一个样本；
缺失环写 <code>width=0, missing=1</code>（missing 接受
1/0、yes/no、true/false、是/否、缺/正常 等）。</p>
<pre>sample_id,unit,start_year,year,width,missing
A01,mm,1950,,1.21,0
A01,,,0.98,0
A01,,,0,1
B01,mm,,,,1.02,0</pre>
<div class="note">校验错误码：E_UNIT_CONFLICT（单位冲突）、
E_NONPOSITIVE_WIDTH（非正宽度且未标缺失）、E_DUPLICATE_YEAR（重复年份）、
E_CONFLICTING_MARK（标了缺失却给正宽度，或零宽度未标记）、E_BAD_YEAR、
E_PARTIAL_YEARS、E_START_CONFLICT、E_EMPTY、E_NO_UNIT。
稀疏年份（跳过若干日历年）不报错，自动补 width=0 缺失环并给 W_IMPLICIT_GAP
警告。加 <code>?strict=1</code> 时先整批校验、任一样本出错则整批回滚（HTTP 422，
数据库中不留任何样本）；不加 strict 时有效样本照常入库、无效样本在 errors 中逐行列出。</div>

<h2>3. 滑动交叉定年 <span class="tag">POST/GET /api/crossdate</span></h2>
<pre>{"sample_id":"B01","reference":"master",
 "offset_min":1900,"offset_max":1990,"min_overlap":30,
 "tolerance":0.05,"top_k":10,"narrow_z":-1.0,"narrow_q":0.1}</pre>
<p><b>offset = 待定样本第 1 轮对应的日历年</b>。服务在
[offset_min, offset_max] 内逐年滑动（缺省自动取参照序列前后各一个样本长度的宽窗口），
重叠不足 <code>min_overlap</code> 年的位置直接剔除。每个候选返回：</p>
<table>
<tr><th>字段</th><th>含义</th></tr>
<tr><td>offset / overlap_start / overlap_end / n_overlap</td>
<td>偏移与实际重叠区间</td></tr>
<tr><td>correlation</td><td>重叠区间原始宽度的 Pearson 相关</td></tr>
<tr><td>sign_agreement</td><td>逐年一阶差分符号一致率（Gleichläufigkeit 式）</td></tr>
<tr><td>narrow_hits</td><td>双方同时为“极窄环”的日历年（z≤narrow_z 且宽度处于
最低 narrow_q 分位；缺失环 0 自动入选）</td></tr></table>
<div class="note">只<b>建议</b>不自动定年：相关最高的候选以及相关差值在
tolerance 内的候选全部保留（至多 top_k），按相关→符号一致率→极窄环命中数排序。
reference 可填 <code>master</code>（当前主年表：已定年样本 + 假设内锁定样本的
逐年标准化均值）或任何已定年样本编号。每次计算持久化到 runs/candidates 表。</div>

<h2>4. 假设与锁定</h2>
<table>
<tr><th>操作</th><th>请求</th></tr>
<tr><td>新建假设</td><td>POST /api/hypotheses {"name":"H1","note":"..."}</td></tr>
<tr><td>锁定偏移</td><td>POST /api/hypotheses/H1/locks
{"sample_id":"B01","offset":1948,"run_id":12}（offset 可用 start_year 别名）</td></tr>
<tr><td>撤销锁定</td><td>DELETE /api/hypotheses/H1/locks?sample_id=B01</td></tr>
<tr><td>假设详情</td><td>GET /api/hypotheses/H1</td></tr>
<tr><td>删除假设</td><td>DELETE /api/hypotheses/H1</td></tr>
<tr><td>假设对比</td><td>GET /api/compare?a=H1&amp;b=H2</td></tr>
<tr><td>对主年表差异</td><td>GET /api/hypotheses/H1/vs-master</td></tr></table>
<div class="note"><b>锁定优先级</b>：锁定总是按假设生效——即使样本上传时带已知
start_year，年表与主年表也一律采用锁定的 offset，旧起始年被实际移开而不是被静默忽略；
两者不一致时年表返回 <code>LOCK_VS_KNOWN</code> 冲突（含 known_start、locked_offset、
shift 年数）。offset 与已知起始年相同则不产生冲突。</div>

<h2>5. 校正草案：缺失环与伪环 <span class="tag">POST /api/corrections</span></h2>
<p>从一次滑动匹配的候选偏移出发，实验员可以声明两类<b>校正事件</b>来修正测量序列与真实生长之间的偏差：</p>
<table>
<tr><th>事件</th><th>含义</th><th>参数</th></tr>
<tr><td><code>missing_ring</code></td>
<td>漏记的缺失环：在测量序号 <code>after_seq</code> 之后插入 <code>years</code> 个缺失日历年
（<code>after_seq=0</code> 表示首测量之前；宽度记 0，参与对比）</td>
<td>after_seq（0..n）、years（默认 1）</td></tr>
<tr><td><code>false_ring</code></td>
<td>误记的伪环：测量 <code>seq</code> 不对应真实日历年，<b>不参与定年</b>
（宽度保留，不分配日历年，不进入相关与主年表）</td>
<td>seq（1..n）</td></tr></table>
<pre>curl -X POST localhost:8000/api/corrections -d '{
  "sample_id":"UNKNOWN_01","offset":1948,"run_id":1,"min_overlap":20,
  "events":[{"type":"missing_ring","after_seq":20},
            {"type":"false_ring","seq":35}]}'</pre>
<p>系统按事件重建<b>分段年份映射</b>（事件把序列切成若干段），分别重算各段及全序列的
重叠区间、Pearson 相关、符号一致率和共同极窄环，并在
<code>changes_vs_candidate</code> 中列出相对原候选的变化。草案、来源运行与
<b>参照快照</b>（创建时冻结的参照年表）都持久化在 SQLite 中。</p>
<table>
<tr><th>操作</th><th>请求</th></tr>
<tr><td>创建草案</td><td>POST /api/corrections（body 见上）</td></tr>
<tr><td>草案列表</td><td>GET /api/corrections?sample_id=UNKNOWN_01</td></tr>
<tr><td>草案详情/预览</td><td>GET /api/corrections/1 或 /api/corrections/1/preview?version=2</td></tr>
<tr><td>新版本</td><td>POST /api/corrections/1/versions {"events":[...]}</td></tr>
<tr><td>版本比较</td><td>GET /api/corrections/1/compare?a=1&amp;b=2</td></tr>
<tr><td>采用</td><td>POST /api/corrections/1/adopt {"hypothesis":"H1","version":2}</td></tr>
<tr><td>撤销</td><td>POST /api/corrections/1/revoke</td></tr>
<tr><td>JSON 导出</td><td>GET /api/corrections/1/download</td></tr></table>
<div class="note"><b>采用校验</b>：事件重复（E_EVENT_DUPLICATE）、次序矛盾
（E_EVENT_ORDER）、超出样本范围（E_EVENT_RANGE）、与已标缺失环冲突
（E_EVENT_VS_MISSING）、与显式年份冲突（E_EVENT_VS_YEAR）时创建即 422 拒绝并
定位测量序号；采用时若任一有效分段不足 min_overlap（E_SEGMENT_TOO_SHORT）同样
422 拒绝并给出受影响测量序号。采用后的分段映射进入指定假设与主年表计算
（chronology 的 <code>corrected_samples</code> 可见），原始宽度、年份与
raw_payload 保持不变；撤销后恢复原 placement。分数接近的校正可并存为多个版本，
系统不自动认定缺失环或伪环。</div>

<h2>6. 年表统计与标记 <span class="tag">GET /api/hypotheses/{name}/chronology</span></h2>
<p>逐年给出 n_samples、mean_index、median_index、stdev_index、mad：</p>
<ul>
<li><b>LOW_COVERAGE</b>：该年样本数 &lt; min_samples（默认 3）。</li>
<li><b>OUTLIER</b>：某样本标准化指数偏离年均值超过 outlier_sd 个标准差（默认 2）。</li>
<li><b>LOCK_CONFLICT</b>：① LOCK_VS_KNOWN——样本锁定偏移与上传时记录的已知起始年
不一致（锁定仍然生效，并给出 shift 年数）；② MISSING_VS_PRESENT——某年多数覆盖样本
都有缺失环而个别样本却有生长轮（物理位置矛盾）；③ WEAK_MATCH——锁定样本在重叠段与其余样本的
leave-one-out 相关低于 weak_correlation（默认 0.3）。</li>
</ul>

<h2>7. 局部稳定性检查 <span class="tag">POST /api/stability</span></h2>
<p>已定年结果在<b>局部区间</b>是否仍然稳定？针对某个假设中<b>已锁定的样本</b>（或已采用校正的
样本）发起检查；不指定 <code>sample_id</code> 时检查假设中全部已锁定/已校正样本。</p>
<pre>curl -X POST localhost:8000/api/stability -d '{
  "hypothesis":"H1","sample_id":"UNKNOWN_01","reference":"master",
  "window":30,"step":10,"min_valid_years":15,
  "run_threshold":3,"search_radius":3,"tolerance":0.05}'</pre>
<p>系统按<b>采用后的年份映射</b>（锁定 offset 或已采用校正草案的分段映射）切出
<code>window</code> 年、步长 <code>step</code> 的重叠窗口：<b>缺失年保留零宽</b>参与统计，
<b>伪环不参与</b>（无日历年）；尾窗的 <code>end_year</code> 收窄到映射实际存在的最大年份，
疑似错位区间不会延伸到不存在的年份。每个窗口在当前位置 <code>±search_radius</code> 年内与参照
（<code>master</code> 或指定样本）滑动比对，逐偏移列出 Pearson 相关、符号一致率与共同极窄环；
与最高相关差值在 <code>tolerance</code> 内的偏移<b>并列保留</b>（<code>tied_shifts</code>）。</p>
<p><b>参照必须独立</b>：参照为 <code>master</code> 时按 <b>leave-one-out</b> 排除被检查样本自身
（快照 meta 的 <code>excluded_samples</code> 可见），杜绝相关值约 1 的自证结果；指定参照样本不能是
被检查目标本身（<code>E_SELF_REFERENCE</code> 422），全假设检查中作为参照的样本自动列入
<code>skipped_targets</code> 并说明原因。目标被排除后若不再有任何独立参照成员，创建即
422（<code>E_NO_INDEPENDENT_REFERENCE</code>）——没有独立参照就不给出稳定性结论。</p>
<p><b>覆盖不足只给依据</b>：窗口有效年数不足 <code>min_valid_years</code>，或所有偏移与参照的
共同年份都不足 <code>min_valid_years</code> 时，该窗口标记 <code>insufficient_coverage</code>
并在 <code>reason</code> 中给出依据（映射年数或最佳共同年数），不下结论。</p>
<p>相邻窗口连续 <code>run_threshold</code> 个偏向<b>同一非零位移</b>时，在
<code>flags</code> 中标出疑似错位区间（起止年份）与涉及的<b>测量序号</b>
（<code>seq_start..seq_end</code>）。shift=+d 表示该区间应整体向晚年方向移动 d 年。</p>
<table>
<tr><th>操作</th><th>请求</th></tr>
<tr><td>创建检查</td><td>POST /api/stability（body 见上）</td></tr>
<tr><td>检查列表</td><td>GET /api/stability?hypothesis=H1</td></tr>
<tr><td>检查详情/窗口筛选</td>
<td>GET /api/stability/1?year_from=1960&amp;year_to=1990&amp;shift=-2&amp;status=ok&amp;sample_id=UNKNOWN_01</td></tr>
<tr><td>两次检查比较</td><td>GET /api/stability/compare?a=1&amp;b=2</td></tr>
<tr><td>JSON 导出</td><td>GET /api/stability/1/download</td></tr></table>
<div class="note"><b>只读保证</b>：检查作业把参数、样本映射与<b>参照快照</b>一并存入 SQLite，
之后复查可还原依据；若创建后锁定或校正发生变化，详情中的
<code>reference_status</code> / <code>mapping_status</code> 只给出 stale 说明（结果仍按创建时
快照评分），<b>不修改任何锁定或校正草案</b>。覆盖不足同样只说明依据。</div>

<h2>8. 序列标准化方案 <span class="tag">POST /api/standardizations</span></h2>
<p>交叉定年与校正确定年份之后，逐样本选择去趋势方法，把原始轮宽转成无量纲<b>轮宽指数</b>
（宽度 / 期望生长曲线）。一个方案以「选定样本 + 各自的方法和参数」为独立数据，维护
<code>draft → validated → adopted → retired</code> 状态，每次修改配置都生成不可变版本。</p>
<pre>curl -X POST localhost:8000/api/standardizations -d '{
  "name":"STD-2026","hypothesis":"H1",
  "samples":[
    {"sample_id":"SITE_A01","method":"negative_exponential"},
    {"sample_id":"UNKNOWN_01","method":"moving_average",
     "parameters":{"window":11}},
    {"sample_id":"SITE_B01","method":"mean"}]}'</pre>
<table>
<tr><th>方法</th><th>method</th><th>参数</th><th>期望曲线</th></tr>
<tr><td>水平均值</td><td><code>mean</code></td><td>—</td><td>全部有效宽度的算术均值（水平线）</td></tr>
<tr><td>负指数曲线</td><td><code>negative_exponential</code></td><td>—</td>
<td><code>a0·exp(b·(year−origin))+d</code>（b≤0），阻尼 Gauss-Newton 拟合</td></tr>
<tr><td>固定窗口居中移动平均</td><td><code>moving_average</code></td>
<td><code>parameters.window</code>（≥3 的奇数）</td>
<td>每个日历年取其居中窗口内有效宽度的均值</td></tr></table>
<p>方案从指定假设<b>当前采用的年份映射</b>读取年份：普通锁定用 offset，已采用校正草案用其分段
映射（并记录来源校正 draft/version）。<b>缺失环保留零宽</b>（指数记 0、参与统计但不参与拟合），
<b>伪环不分配日历年、不参与拟合</b>。只有正宽度且已定年的点才是拟合有效点（至少 3 个）。</p>
<div class="note"><b>不自动更换算法</b>：有效点不足（<code>E_TOO_FEW_VALID_POINTS</code>，返回
<code>seq</code> 与 <code>seqs</code>/<code>missing_seqs</code> 定位测量位置）、负指数不收敛
（<code>E_EXP_NOT_CONVERGED</code>）、期望值非正（<code>E_EXPECTED_NONPOSITIVE</code>）或窗口内
数据不足（<code>E_WINDOW_TOO_SHORT</code>）时，422 逐样本给出<b>样本号与测量序号</b>，保留实验员
选择的方法，绝不偷偷换成别的曲线。</div>
<table>
<tr><th>操作</th><th>请求</th></tr>
<tr><td>创建方案（draft v1）</td><td>POST /api/standardizations（body 见上）</td></tr>
<tr><td>方案列表</td><td>GET /api/standardizations?status=draft&amp;hypothesis=H1</td></tr>
<tr><td>详情/预览版本</td><td>GET /api/standardizations/1 或 ?version=2</td></tr>
<tr><td>候选配置临时试算</td><td>POST /api/standardizations/1/preview（body 带 samples，不建版本）</td></tr>
<tr><td>新版本 / 版本列表</td>
<td>POST /api/standardizations/1/versions {"samples":[...]}；GET 同路径列版本</td></tr>
<tr><td>全样本校验</td><td>POST /api/standardizations/1/validate（全部通过才 draft→validated）</td></tr>
<tr><td>采用（冻结）</td><td>POST /api/standardizations/1/adopt（可带 version，默认最新）</td></tr>
<tr><td>停用</td><td>POST /api/standardizations/1/retire</td></tr>
<tr><td>版本比较</td><td>GET /api/standardizations/1/compare?a=1&amp;b=2</td></tr>
<tr><td>JSON 下载</td><td>GET /api/standardizations/1/download（?download=0 取消附件头）</td></tr></table>
<p><b>采用即冻结</b>：adopt 时再次全样本校验，通过后把来源映射（含校正版本）、参数、每条期望曲线、
指数与诊断一起冻结到该版本。主年表、滑动匹配与局部稳定性检查都可用
<code>standardization_id</code>（别名 <code>standardization</code>）+
<code>standardization_version</code> 指定一个已采用版本：
<code>GET /api/master?hypothesis=H1&amp;standardization_id=1</code>、
<code>POST /api/crossdate</code> / <code>POST /api/stability</code> 在 body 带
<code>{"standardization_id":1}</code>。不传时保持原原始宽度/样本均值口径，<b>旧作业仍保留其计算依据</b>。
来源映射之后若变化，详情中的 <code>source_status</code> 只给 stale 说明，已冻结指数不变。</p>

<h2>9. 年表信号强度评估（Rbar / EPS） <span class="tag">POST /api/signal</span></h2>
<p>年表的<b>群体信号</b>够不够强？评估在创建瞬间<b>冻结计算依据</b>：定年假设、可选的已采用标准化版本、
<b>样本集合</b>与窗口参数，以及每个成员样本按当时年份映射算出的无量纲指数（原始口径为
宽度/正宽均值；标准化口径直接取方案冻结指数）。之后锁定、校正或标准化版本变化，只把旧评估标记为
<code>source_status.stale</code>，<b>绝不重算</b>已保存的统计。</p>
<pre>curl -X POST localhost:8000/api/signal -d '{
  "name":"EPS-2026","hypothesis":"H1",
  "samples":["SITE_A01","SITE_A02","SITE_B01","SITE_B02","SITE_C01"],
  "window":50,"step":25,"min_samples":3,
  "eps_threshold":0.85,"min_pair_years":5}'</pre>
<p>在每个窗口内按成员<b>共同年份</b>给出：</p>
<table>
<tr><th>量</th><th>含义</th></tr>
<tr><td>样本深度 depth</td><td>逐年覆盖样本数，窗口给出 max_depth、mean_depth 与逐年 depth_inputs</td></tr>
<tr><td>样本对相关</td><td>每对成员在<b>仅属于该窗口年份范围</b>内的共同年份上的 Pearson（绝不跨窗口取整段共同年份）；窗口内共同年份 &lt; min_pair_years 或任一方零方差时
记为<b>无效配对</b>并说明原因（相关仍作为依据列出，但不喂给 Rbar）</td></tr>
<tr><td>Rbar（R̄）</td><td>有效配对相关的算术平均（n_valid_pairs 个）</td></tr>
<tr><td>EPS</td><td><code>N·R̄ / (N·R̄ + 1 − R̄)</code>，N 为窗口平均样本深度；分母非正时 eps_incalculable</td></tr></table>
<div class="note"><b>只给依据、不自动排除</b>：覆盖不足（平均深度不足 min_samples）标记
<code>insufficient_coverage</code>、无有效配对 <code>no_valid_pairs</code>、EPS 无法计算
<code>eps_incalculable</code>——这些窗口<b>不下 Rbar/EPS 结论</b>，只返回参与样本、配对明细与深度输入。
每个达标窗口还做 <b>jackknife</b>：逐个剔除样本重算 Rbar、EPS 与差值（<code>jackknife[].eps_delta</code>），
差值仅供参考，<b>没有任何序列会被自动剔除</b>。</div>
<p>状态在 <code>draft → completed → adopted → retired</code> 间流转（也可直接停用）：</p>
<table>
<tr><th>操作</th><th>请求</th></tr>
<tr><td>创建评估（draft）</td><td>POST /api/signal（body 见上；samples 可省略=假设内全部已定年样本）</td></tr>
<tr><td>完成</td><td>POST /api/signal/1/complete（draft→completed，冻结证据不变）</td></tr>
<tr><td>采用可靠区间</td><td>POST /api/signal/1/adopt（completed→adopted）</td></tr>
<tr><td>停用</td><td>POST /api/signal/1/retire</td></tr>
<tr><td>列表/筛选</td><td>GET /api/signal?hypothesis=H1&amp;status=adopted&amp;sample_id=SITE_A01</td></tr>
<tr><td>详情/窗口筛选</td><td>GET /api/signal/1?year_from=1960&amp;year_to=2000&amp;eps_pass=1&amp;status=ok&amp;sample_id=SITE_A01</td></tr>
<tr><td>两次评估比较</td><td>GET /api/signal/compare?a=1&amp;b=2</td></tr>
<tr><td>JSON 下载</td><td>GET /api/signal/1/download（?download=0 取消附件头）</td></tr></table>
<p><b>可靠区间只能是连续达标的窗口</b>：adopt 时可在 body 给
<code>{"windows":[2,3,4]}</code> 指定<b>相邻且全部达标</b>的窗口下标；缺省取最长连续达标段，自动给出
<code>reliable_span</code>（窗口下标 + 起止日历年）。未达 <code>eps_threshold</code> 的窗口永远不得采用
（<code>E_WINDOW_BELOW_THRESHOLD</code>）；不存在任何达标窗口时 422（<code>E_NO_RELIABLE_RANGE</code>）。
仍是 draft 不能采用（<code>E_SIGNAL_DRAFT</code>，先 complete）。<b>采用是一次性不可变操作</b>：一旦 adopted，
可靠区间与版本号即固定，再次调用 adopt（即使 windows 不同）返回
<code>E_SIGNAL_ALREADY_ADOPTED</code>，原区间不变；要更换区间须新建评估并停用旧评估。</p>
<p><b>已采用评估限制下游参照</b>：主年表、滑动匹配（及年表/报告）可带
<code>signal_id</code>（别名 <code>signal_assessment</code>）指定一个 adopted 评估版本——参照只取该评估的
<b>冻结成员指数</b>并把年份限制在 <code>reliable_span</code> 内，候选的重叠区间不会越出可靠范围；
不传时保持原范围与原口径，<b>旧作业保持其原范围</b>（runs 表持久化 signal_id/version；评估停用后
旧运行记录不变，只是不能再新挂该评估）。</p>
<pre>curl 'localhost:8000/api/master?hypothesis=H1&amp;signal_id=1'
curl -X POST localhost:8000/api/crossdate -d '{"sample_id":"SITE_C02",
  "hypothesis":"H1","signal_id":1,"offset_min":1935,"offset_max":1945}'</pre>

<h2>10. 报告与主年表</h2>
<table>
<tr><td>下载 JSON 报告</td>
<td>GET /api/hypotheses/H1/report?download=1</td></tr>
<tr><td>在线报告</td><td>GET /api/hypotheses/H1/report</td></tr>
<tr><td>当前主年表</td><td>GET /api/master?hypothesis=H1</td></tr>
<tr><td>标准化主年表</td><td>GET /api/master?hypothesis=H1&amp;standardization_id=1</td></tr>
<tr><td>历史滑动结果</td><td>GET /api/runs</td></tr></table>

<h2>11. cURL 速览</h2>
<pre>curl -X POST localhost:8000/api/series \\
  -H 'Content-Type: text/csv' --data-binary @examples/samples.csv
curl -X POST localhost:8000/api/hypotheses -d '{"name":"H1"}'
curl -X POST localhost:8000/api/crossdate \\
  -d '{"sample_id":"UNKNOWN_01","reference":"SITE_A","min_overlap":30}'
curl -X POST localhost:8000/api/hypotheses/H1/locks \\
  -d '{"sample_id":"UNKNOWN_01","offset":1952}'
curl -X POST localhost:8000/api/corrections \\
  -d '{"sample_id":"UNKNOWN_01","offset":1948,"run_id":1,
       "events":[{"type":"missing_ring","after_seq":20},
                 {"type":"false_ring","seq":35}]}'
curl -X POST localhost:8000/api/corrections/1/adopt -d '{"hypothesis":"H1"}'
curl -X POST localhost:8000/api/standardizations \\
  -d '{"name":"STD-2026","hypothesis":"H1","samples":[
       {"sample_id":"SITE_A01","method":"negative_exponential"}]}'
curl -X POST localhost:8000/api/standardizations/1/validate
curl -X POST localhost:8000/api/standardizations/1/adopt
curl -X POST localhost:8000/api/stability \\
  -d '{"hypothesis":"H1","sample_id":"UNKNOWN_01","standardization_id":1,
       "window":30,"step":10,"run_threshold":3,"search_radius":3}'
curl 'localhost:8000/api/master?hypothesis=H1&amp;standardization_id=1'
curl -X POST localhost:8000/api/signal \\
  -d '{"name":"EPS-2026","hypothesis":"H1","window":50,"step":25,
       "min_samples":3,"eps_threshold":0.85}'
curl -X POST localhost:8000/api/signal/1/complete
curl -X POST localhost:8000/api/signal/1/adopt
curl 'localhost:8000/api/master?hypothesis=H1&amp;signal_id=1'
curl 'localhost:8000/api/signal/compare?a=1&amp;b=2'
curl 'localhost:8000/api/stability/1?shift=-2&amp;status=ok'
curl 'localhost:8000/api/stability/compare?a=1&amp;b=2'
curl 'localhost:8000/api/hypotheses/H1/report?download=1' -o report.json</pre>
</body></html>
"""
