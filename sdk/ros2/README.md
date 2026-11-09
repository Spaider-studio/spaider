# spaider-ros2

Subscribes to ROS 2 topics and stores messages as episodes in
[`spaider-edge`](../edge). Syncs to a SpAIder server when one is reachable and
answers recall requests on a topic.

```python
from spaider_edge import EdgeMemory
from spaider_ros2 import MemoryBridge, TopicMapping
from std_msgs.msg import String

bridge = MemoryBridge(node, EdgeMemory("robot.db"), [TopicMapping("/events", String, kind="event")])
```

Publish a query as `std_msgs/String` on `/spaider/recall`. Results arrive as JSON
on `/spaider/recall_result`.

**Testing status:** this bridge has only been tested against fake `rclpy` objects
in `tests/`. It has not been run on a real ROS 2 installation or a robot.
