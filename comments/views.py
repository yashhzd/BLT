import json
import os
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.http import HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import HttpResponse, render
from django.template.loader import render_to_string
from django.utils.html import escape

from website.models import Issue

from .models import Comment

# Only allow alphanumeric, underscore, dot for JSONP callback names
VALID_CALLBACK_RE = re.compile(r"^[a-zA-Z_$][a-zA-Z0-9_$.]*$")


def _process_mentions(text):
    """Parse @mentions from text, return (processed_html, plain_msg, mentioned_users)."""
    escaped = escape(text)
    tokens = escaped.split()
    text_parts = []
    msg_parts = []
    mentioned_users = []
    for token in tokens:
        msg = token
        if token and token[0] == "@" and len(token) > 1:
            username = token[1:]
            try:
                user = User.objects.get(username=username)
                mentioned_users.append(user)
                msg = user.username
                safe_username = escape(user.username)
                token = "<a href='/profile/{0}'>@{1}</a>".format(safe_username, safe_username)
            except User.DoesNotExist:
                pass
        text_parts.append(token)
        msg_parts.append(msg)
    return " ".join(text_parts), " ".join(msg_parts), mentioned_users


def _notify_mentioned_users(mentioned_users, request_user, issue_pk, plain_msg):
    """Send email notifications to mentioned users."""
    for obj in mentioned_users:
        msg_plain = render_to_string(
            "email/comment_mention.html",
            {
                "name": obj.username,
                "commentor": request_user,
                "issue_pk": issue_pk,
                "comment": plain_msg,
            },
        )
        msg_html = render_to_string(
            "email/comment_mention.html",
            {
                "name": obj.username,
                "commentor": request_user,
                "issue_pk": issue_pk,
                "comment": plain_msg,
            },
        )

        send_mail(
            "You have been mentioned in a comment",
            msg_plain,
            settings.EMAIL_TO_STRING,
            [obj.email],
            html_message=msg_html,
        )


@login_required(login_url="/accounts/login/")
def add_comment(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    pk = request.POST.get("issue_pk")
    if not pk:
        return HttpResponseBadRequest("Missing issue_pk")

    try:
        issue = Issue.objects.get(pk=pk)
    except Issue.DoesNotExist:
        return HttpResponseBadRequest("Issue not found")

    author = request.user.username
    author_url = os.path.join("/profile/", request.user.username)
    text = request.POST.get("text_comment", "")
    new_text, new_msg, mentioned_users = _process_mentions(text)
    _notify_mentioned_users(mentioned_users, request.user, pk, new_msg)

    comment = Comment(author=author, author_url=author_url, issue=issue, text=new_text)
    comment.save()
    all_comment = Comment.objects.filter(issue=issue)
    return render(
        request,
        "comments.html",
        {"all_comment": all_comment, "user": request.user},
    )


@login_required(login_url="/accounts/login")
def delete_comment(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        issue = Issue.objects.get(pk=request.POST.get("issue_pk"))
    except (Issue.DoesNotExist, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid issue")

    all_comment = Comment.objects.filter(issue=issue)

    try:
        comment = Comment.objects.get(pk=int(request.POST.get("comment_pk", 0)))
    except (Comment.DoesNotExist, ValueError, TypeError):
        return HttpResponseBadRequest("Comment not found")

    try:
        show = comment.parent.pk
    except Exception:
        show = -1

    if request.user.username != comment.author:
        return HttpResponseForbidden("Cannot delete this comment")

    comment.delete()
    return render(
        request,
        "comments.html",
        {
            "all_comment": all_comment,
            "user": request.user,
            "show": show,
        },
    )


@login_required(login_url="/accounts/login/")
def edit_comment(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    issue_pk = request.POST.get("issue_pk")
    if str(pk) != str(issue_pk):
        return HttpResponseBadRequest("URL issue ID does not match POST body")

    try:
        issue = Issue.objects.get(pk=issue_pk)
    except (Issue.DoesNotExist, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid issue")

    try:
        comment = Comment.objects.get(pk=request.POST.get("comment_pk"))
    except (Comment.DoesNotExist, ValueError, TypeError):
        return HttpResponseBadRequest("Comment not found")

    if request.user.username != comment.author:
        return HttpResponseForbidden("Cannot edit this comment")

    comment.text = escape(request.POST.get("text_comment", ""))
    comment.save()
    all_comment = Comment.objects.filter(issue=issue)
    return render(
        request,
        "comments.html",
        {"all_comment": all_comment, "user": request.user},
    )


@login_required(login_url="/accounts/login/")
def reply_comment(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    issue_pk = request.POST.get("issue_pk")
    if str(pk) != str(issue_pk):
        return HttpResponseBadRequest("URL issue ID does not match POST body")

    parent_id = request.POST.get("parent_id")
    if not parent_id:
        return HttpResponseBadRequest("Missing parent_id")

    try:
        show = int(parent_id)
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Invalid parent_id")

    try:
        parent_obj = Comment.objects.get(id=parent_id)
    except Comment.DoesNotExist:
        return HttpResponseBadRequest("Parent comment not found")

    author = request.user.username
    author_url = os.path.join("/profile/", request.user.username)

    try:
        issue = Issue.objects.get(pk=issue_pk)
    except (Issue.DoesNotExist, ValueError, TypeError):
        return HttpResponseBadRequest("Invalid issue")

    reply_text = request.POST.get("text_comment", "")
    new_text, new_msg, mentioned_users = _process_mentions(reply_text)
    _notify_mentioned_users(mentioned_users, request.user, issue_pk, new_msg)

    comment = Comment(author=author, author_url=author_url, issue=issue, text=new_text, parent=parent_obj)
    comment.save()
    all_comment = Comment.objects.filter(issue=issue)
    return render(
        request,
        "comments.html",
        {"all_comment": all_comment, "user": request.user, "show": show},
    )


@login_required(login_url="/accounts/login")
def autocomplete(request):
    callback = request.GET.get("callback", "")
    if not callback or not VALID_CALLBACK_RE.match(callback):
        return HttpResponseBadRequest("Invalid callback parameter")

    q_string = request.GET.get("search", "")
    q_string = escape(q_string)
    if len(q_string) == 0:
        return HttpResponse(callback + "(" + json.dumps([]) + ");", content_type="application/json")

    q_list = q_string.split(" ")
    q_s = q_list[-1]
    if len(q_s) == 0 or q_s[0] != "@":
        return HttpResponse(callback + "(" + json.dumps([]) + ");", content_type="application/json")

    q_s = q_s[1:]
    search_qs = User.objects.filter(username__startswith=q_s)[:20]
    results = [r.username for r in search_qs]
    resp = callback + "(" + json.dumps(results) + ");"
    return HttpResponse(resp, content_type="application/json")
