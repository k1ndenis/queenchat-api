class TestNotificationsAPI:
    def test_get_notifications_empty(self, auth_client):
        response = auth_client.get("/api/notifications/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert len(response.json()) == 0

    def test_create_and_get_notification(self, auth_client, db_session):
        from app.services.notification_service import NotificationService
        
        service = NotificationService(db_session)
        
        if hasattr(service, 'create'):
            notification = service.create(
                user_id=auth_client.user_id,
                chat_id="chat123",
                title="Test",
                message="Test message",
                type="info"
            )
        elif hasattr(service, 'create_notification'):
            notification = service.create_notification(
                user_id=auth_client.user_id,
                chat_id="chat123",
                title="Test",
                message="Test message",
                type="info"
            )
        else:
            from app.repositories.notification_repository import NotificationRepository
            repo = NotificationRepository(db_session)
            notification = repo.create(
                user_id=auth_client.user_id,
                chat_id="chat123",
                title="Test",
                message="Test message",
                type="info"
            )
        
        response = auth_client.get("/api/notifications/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_get_unread_count(self, auth_client, db_session, notification_service):
        notification_service.create_notification(
            user_id=auth_client.user_id,
            chat_id="test-chat-id",
            title="Unread",
            message="Unread message",
            type="info"
        )
        
        response = auth_client.get("/api/notifications/unread/count")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert data["count"] >= 1

    def test_mark_as_read(self, auth_client, db_session, notification_service):
        notification = notification_service.create_notification(
            user_id=auth_client.user_id,
            chat_id="test-chat-id",
            title="To Read",
            message="Mark as read test",
            type="info"
        )
        
        response = auth_client.patch(f"/api/notifications/{notification.id}/read")
        assert response.status_code == 200
        
        response = auth_client.get("/api/notifications/")
        notifications = response.json()
        found = next((n for n in notifications if n["id"] == notification.id), None)
        assert found is not None
        assert found["is_read"] is True

    def test_mark_all_as_read(self, auth_client, db_session, notification_service):
        for i in range(3):
            notification_service.create_notification(
                user_id=auth_client.user_id,
                chat_id="test-chat-id",
                title=f"Test {i}",
                message=f"Message {i}",
                type="info"
            )
        
        response = auth_client.patch("/api/notifications/read/all")
        assert response.status_code == 200
        
        response = auth_client.get("/api/notifications/")
        notifications = response.json()
        assert all(n["is_read"] is True for n in notifications)

    def test_clean_old_notifications(self, auth_client, db_session, notification_service):
        from app.core.database import NotificationORM
        import time
        
        old_notification = NotificationORM(
            id="old-id",
            user_id=auth_client.user_id,
            chat_id="test-chat-id",
            title="Old",
            message="Old message",
            type="info",
            is_read=True,
            created_at=int(time.time()) - (40 * 86400)
        )
        db_session.add(old_notification)
        db_session.commit()
        
        response = auth_client.delete("/api/notifications/clean/old?days=30")
        assert response.status_code == 200
        assert "deleted" in response.json()
        
        response = auth_client.get("/api/notifications/")
        notifications = response.json()
        assert all(n["id"] != "old-id" for n in notifications)

    def test_clean_read_notifications(self, auth_client, db_session, notification_service):
        read_notification = notification_service.create_notification(
            user_id=auth_client.user_id,
            chat_id="test-chat-id",
            title="Read",
            message="Read message",
            type="info"
        )
        notification_service.mark_as_read(read_notification.id, auth_client.user_id)
        
        response = auth_client.delete("/api/notifications/clean/read")
        assert response.status_code == 200
        assert "deleted" in response.json()
        
        response = auth_client.get("/api/notifications/")
        notifications = response.json()
        assert all(n["id"] != read_notification.id for n in notifications)