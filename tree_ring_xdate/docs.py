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

<h2>6. 报告与主年表</h2>
<table>
<tr><td>下载 JSON 报告</td>
<td>GET /api/hypotheses/H1/report?download=1</td></tr>
<tr><td>在线报告</td><td>GET /api/hypotheses/H1/report</td></tr>
<tr><td>当前主年表</td><td>GET /api/master?hypothesis=H1</td></tr>
<tr><td>历史滑动结果</td><td>GET /api/runs</td></tr></table>

<h2>7. 报告与主年表</h2>
<table>
<tr><td>下载 JSON 报告</td>
<td>GET /api/hypotheses/H1/report?download=1</td></tr>
<tr><td>在线报告</td><td>GET /api/hypotheses/H1/report</td></tr>
<tr><td>当前主年表</td><td>GET /api/master?hypothesis=H1</td></tr>
<tr><td>历史滑动结果</td><td>GET /api/runs</td></tr></table>

<h2>8. cURL 速览</h2>
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
curl 'localhost:8000/api/hypotheses/H1/report?download=1' -o report.json</pre>
</body></html>
"""
