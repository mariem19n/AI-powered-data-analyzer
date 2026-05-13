export default function UserBubble({ content }) {
  return (
    <div className="message-row user">
      <div className="user-bubble">
        <p>{content}</p>
      </div>
    </div>
  );
}
