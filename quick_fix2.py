c = open("D:/PycharmProjects/cloth-system/order/views.py","r",encoding="utf-8").read()
# Fix: "t(" -> t(  and ")" -> )
c = c.replace('": "t("', '": t("')
c = c.replace('")"', '")')
c = c.replace('")', '")')
# More precise: the pattern is "t("page.task_initializing")" -> t("page.task_initializing")
c = c.replace('"t("page.task_initializing")"', 't("page.task_initializing")')
open("D:/PycharmProjects/cloth-system/order/views.py","w",encoding="utf-8").write(c)
try:
    compile(c, "test.py", "exec")
    print("COMPILE OK")
except SyntaxError as e:
    print(f"L{e.lineno}: {e.msg}")
