import XCTest
@testable import AideShell

final class MessageTextTests: XCTestCase {
    func testHeadingsListsQuotesAndTablesAreRead() {
        let text = """
        ## 步骤

        先说明一下：
        1. 安装
        2. 运行
        - 备注一
        - 备注二

        > 引用内容

        | 名称 | 数量 |
        | --- | ---: |
        | 苹果 | 3 |
        | 梨 | 5 |

        ```swift
        let x = 1
        ```
        结束
        """
        XCTAssertEqual(MessageText.blocks(text), [
            .heading(level: 2, text: "步骤"),
            .prose("先说明一下："),
            .list(ordered: true, items: ["安装", "运行"]),
            .list(ordered: false, items: ["备注一", "备注二"]),
            .quote("引用内容"),
            .table(header: ["名称", "数量"], rows: [["苹果", "3"], ["梨", "5"]]),
            .code(language: "swift", body: "let x = 1"),
            .prose("结束"),
        ])
    }

    func testOrdinaryTextIsNotMistakenForStructure() {
        XCTAssertEqual(MessageText.blocks("价格是 3.5 元，a | b 不是表格。\n#标签 不是标题\n3.14 也不是列表"),
                       [.prose("价格是 3.5 元，a | b 不是表格。\n#标签 不是标题\n3.14 也不是列表")])
    }

    func testUnfinishedTableWhileStreamingStaysReadable() {
        XCTAssertEqual(MessageText.blocks("| a | b |"), [.prose("| a | b |")])
    }
}
