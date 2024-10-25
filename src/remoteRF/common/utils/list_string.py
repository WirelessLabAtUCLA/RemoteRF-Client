def list_to_str(list:list) -> str:
    return ','.join(str(x) for x in list)

def str_to_list(s:str) -> list[int]:
    return [int(x) for x in s.split(',')]