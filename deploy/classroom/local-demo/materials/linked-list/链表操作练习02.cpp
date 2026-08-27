/*课后习题
	单链表操作：
	完善下面的带头结点的单向链表类的相关成员函数，
	(1)向链表尾部插入的成员函数
	   void insertToTail(int val);
	(2)写出将链表倒置的成员函数
		void Reverse();
*/
#include <iostream>
using namespace std;
class Node //链表的结点定义
{
public:
    Node(int x)
    {
        data = x;
        next = NULL;
    }
    int data;
    Node* next;
};
class MList //带有头结点的单向链表类定义
{
private:
    Node* head;//指向头结点，不是实际的数据结点

public:
    MList();
    ~MList();
    void insertToTail(int val);//TODO1:在类外给出该函数实现——向尾部插入数据val
    void Reverse();//TODO2:在类外给出该函数实现——翻转链表
    void print();
};
//不需要改变下面的构造函数
MList::MList()
{
    head = new Node(0);//head 指向的是头结点
}
//不需要改变下面的析构函数
MList::~MList()
{
    Node* temp = head;
    while(temp) //逐个释放结点空间
    {
        head = head ->next;
        delete temp;
        temp = head;
    }
    head = NULL;
}

//不要改变下面的print函数
void MList::print()
{
    Node* p = head->next;
    while (p) {
        cout << p->data<< " ";
        p = p ->next;
    }
}
//不要改变下面的main函数
int main()
{
    MList lt;//创建链表对象 lt
    int Num;//Num 表示要输入的元素的个数
    cin >> Num;
    for (int i = 0; i < Num; i++) {
        int val;
        cin >> val;
        lt.insertToTail(val);
    }
    cout << "倒置前为：";
    lt.print();
    cout << endl;
    lt.Reverse();
    cout << "倒置后为：";
    lt.print();
    cout << endl;
    return 0;
}
