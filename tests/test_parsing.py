from __future__ import annotations

from pathlib import Path

from chathsr.parsing import find_category_slug, parse_article, parse_board_posts


FIXTURES = Path(__file__).parent / "fixtures"


def test_find_category_slug() -> None:
    html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    slug = find_category_slug(
        html,
        board_url="https://arca.live/b/hkstarrail",
        category_label="정보",
    )
    assert slug == "정보"


def test_find_category_slug_accepts_decorated_category_labels() -> None:
    html = """
    <html>
      <body>
        <div class="board-category">
          <a href="/b/hkstarrail?category=%EC%A0%95%EB%B3%B4">
            <span>정보</span>
            <small>1,234</small>
          </a>
        </div>
      </body>
    </html>
    """
    slug = find_category_slug(
        html,
        board_url="https://arca.live/b/hkstarrail",
        category_label="정보",
    )
    assert slug == "정보"


def test_parse_board_posts_marks_notice_and_extracts_posts() -> None:
    html = (FIXTURES / "board_page.html").read_text(encoding="utf-8")
    posts = parse_board_posts(html, board_url="https://arca.live/b/hkstarrail")
    assert len(posts) == 3
    assert posts[0].is_notice is True
    assert posts[1].post_id == 12345678
    assert posts[2].post_id == 12340000


def test_parse_article_extracts_body_and_images() -> None:
    html = (FIXTURES / "article_page.html").read_text(encoding="utf-8")
    article = parse_article(html, url="https://arca.live/b/hkstarrail/12345678")
    assert article.title == "반디 픽업 정리"
    assert article.category_label == "정보"
    assert article.author == "테스트작성자"
    assert "반디는 격파 특화 딜러다." in article.body_text
    assert "속도 150 이상" in article.body_text
    assert article.image_urls == ["https://arca.live/files/guide.png"]


def test_parse_article_allows_image_only_posts() -> None:
    html = """
    <html>
      <body>
        <div class="article-head">
          <div class="title">
            <span class="badge badge-success">정보</span>
            이미지 공지
          </div>
        </div>
        <div class="article-content">
          <p><img src="/files/image-only.png" /></p>
        </div>
      </body>
    </html>
    """
    article = parse_article(html, url="https://arca.live/b/hkstarrail/23456789")
    assert article.body_text == ""
    assert article.image_urls == ["https://arca.live/files/image-only.png"]
