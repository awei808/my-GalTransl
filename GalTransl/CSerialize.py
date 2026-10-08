import orjson
from typing import Optional

from GalTransl.CSentense import CTransList
from GalTransl.Utils import restore_output_line_breaks


def save_transList_to_json_cn(trans_list: CTransList, save_path: str, name_dict: Optional[dict] = None) -> None:
    """按译后列表整体写出 output JSON（换行按每句原文类型还原）。"""
    if name_dict is None:
        name_dict = {}
    result_list = []
    for tran in trans_list:
        # 换行口径与桌面端构建输出一致：缓存里的真实 \n 还原为原文所用换行符
        message = restore_output_line_breaks(tran.post_dst, tran.pre_src)
        if tran._speaker != "":
            if type(tran._speaker) == list:
                result_name = []
                for name in tran._speaker:
                    result_name.append(name_dict[name] if name in name_dict else name)
                result_list.append({"names": result_name, "message": message})
            else:
                result_name = (
                    name_dict[tran._speaker]
                    if tran._speaker in name_dict
                    else tran._speaker
                )
                result_list.append({"name": result_name, "message": message})
        else:
            result_list.append({"message": message})
    with open(save_path, "wb") as f:
        f.write(orjson.dumps(result_list, option=orjson.OPT_INDENT_2))


def update_json_with_transList(
    trans_list: CTransList, old_json_list: list, name_dict: Optional[dict] = None
) -> list:
    """把译文合并回原始 input JSON（换行按每句原文类型还原）。"""
    if name_dict is None:
        name_dict = {}
    result_json_list = old_json_list.copy()
    # Iterate over the old JSON data and the trans_list simultaneously
    for old_item, tran in zip(result_json_list, trans_list):
        # Check if the 'message' in the old JSON data matches with 'pre_src' in the tran
        if old_item.get("message") == tran.pre_src:
            # Update the 'message' field（换行还原：与桌面端构建输出同口径）
            old_item["message"] = restore_output_line_breaks(tran.post_dst, tran.pre_src)

            # Update the 'name' field if it exists and tran._speaker is not a list
            if "name" in old_item and not isinstance(tran._speaker, list):
                old_item["name"] = (
                    name_dict[tran._speaker]
                    if tran._speaker in name_dict
                    else tran._speaker
                )

            # Update the 'names' field if it exists and tran._speaker is a list
            if "names" in old_item and isinstance(tran._speaker, list):
                old_item["names"] = [
                    name_dict[name] if name in name_dict else name
                    for name in tran._speaker
                ]

    return result_json_list


def save_json(file_path: str, result_json: list) -> None:
    with open(file_path, "wb") as f:
        f.write(orjson.dumps(result_json, option=orjson.OPT_INDENT_2))
