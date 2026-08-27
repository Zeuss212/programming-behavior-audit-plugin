/*课后习题

	a.	完善存储整型数据的顺序表的类定义，完善基本的成员函数，并给出以下功能函数的具体实现。
		i.	从顺序表中删除具有最小值的元素并由函数返回被删元素的值，空出的位置由最后一个元素填补
			int deletemin();

		ii.	从顺序表中删除与给定x相等的所有元素
			void deleteSame(int x);

		iii.从顺序表中删除其值在给定 s与t之间（s < t）的所有元素,不包括s和t
			void deleteSome(int s, int t);
*/
#include <iostream>
using namespace std;
class  SeqArray  //顺序表
{
private:
    int* arr; //数组的起始地址
    int N;//数组规模
    int n;//数组当前元素个数
public:
    SeqArray(int NN=10);
    ~SeqArray();
    bool insertElement(int value);//向顺序表中插入value,如果成功返回true，否则返回false
    int deletemin();
    void deleteSame(int x);
    void deleteSome(int s, int t);
    void print(); //输出顺序表的数据
};
//请给出各个成员函数的具体实现
SeqArray::SeqArray(int NN)
{
    //todo-请给出具体实现代码

}
SeqArray::~SeqArray()
{
   //todo-请给出具体实现代码
}
bool SeqArray::insertElement(int value)
{
    //todo-请给出具体实现代码
}
int SeqArray::deletemin()
{
  //todo-请给出具体实现代码
}
void  SeqArray::deleteSame(int x)
{
    //todo-请给出具体实现代码
}
void  SeqArray::deleteSome(int s,int t)
{
    //todo-请给出具体实现代码
}
void SeqArray::print()
{
//todo-请给出具体实现代码

}

//请不要修改下面main函数的函数体
int main()
{
    // n 表示要输入的数据元素个数， minval记录删除的最小值，
    //samevalue表示指定删除的数据，s、t表示要删除的数据的范围s<t
    int n,minval,samevalue, s, t;

    SeqArray a(20);
    cin>>n;
    for (int i = 0; i < n; i++) {
        cin >> value;
        a.insertElement(value);
    }

    cout << "顺序表数据为:";
    a.print();
    minval = a.deletemin();
    cout << "删除最小值后为:";
    a.print();
    cout << "最小值:" << minval << endl;
    cin >> samevalue;
    a.deleteSame(samevalue);
    cout << "删除相同值后为:";
    a.print();
    cin >> s >> t;
    a.deleteSome(s, t);
    cout << "删除指定范围数值后为:";
    a.print();
    return 0;
}
